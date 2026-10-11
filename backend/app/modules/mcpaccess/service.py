"""MCP connections: who an external agent is, what it may query, and how it asks.

McpAccessService manages connections (admin) and authenticates calls. AgentService is what
the MCP tools call: it adapts the shared ChatService and never reimplements question
answering. Neither knows the MCP protocol; app.mcp_gateway does.
"""

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.audit import AuditService
from app.core.deps import CurrentUser, Role
from app.core.errors import MESSAGES, AppError, ErrorCode
from app.core.response import PageParams
from app.core.stream import EventType
from app.modules.auth.service import require_admin
from app.modules.chat.service import ChatService, ConversationCreate, QuestionRequest, plain_rows
from app.modules.datasource.service import DatasourceService
from app.modules.mcpaccess.models import McpClient
from app.modules.mcpaccess.repository import McpRepository
from app.modules.mcpaccess.schemas import (
    CHAR_BUDGET,
    AgentDataset,
    AgentError,
    AgentMetric,
    AgentUsage,
    AskResult,
    McpClientRead,
    McpClientWrite,
    McpKeyCreated,
    McpKeyRead,
    OverlayItem,
    OverrideRestore,
    OverrideWrite,
    SqlResult,
    TestAskReply,
    TestAskRequest,
    TestDiagnostics,
)
from app.modules.metric.service import MetricService, MetricView, render_template
from app.modules.org.service import OrgService
from app.modules.permission.service import ClientGrantsRead, ClientGrantsWrite, GrantService
from app.modules.semantic.service import SemanticService, SemanticView

KEY_PREFIX = "ak_"
# A conversation an agent has not used for this long can no longer be continued.
CONVERSATION_TTL = timedelta(days=7)
SEMANTIC_FIELDS = ("aliases", "description", "enum_mapping")
METRIC_FIELDS = ("aliases", "description", "sql_template", "params")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_request_id() -> str:
    return f"rq_{secrets.token_hex(8)}"


@dataclass(frozen=True)
class McpPrincipal:
    """The authenticated caller of one MCP request. Built only from a verified key."""

    user: CurrentUser
    client_id: int
    public_id: str
    name: str
    default_rows: int
    max_rows: int
    ask_timeout_seconds: int
    allow_run_sql: bool


@dataclass
class CallRef:
    """Identifiers of one tool call: ours (request_id) and the client's JSON-RPC id."""

    request_id: str = field(default_factory=new_request_id)
    rpc_id: str | None = None
    source: Literal["mcp", "mcp_test"] = "mcp"

    def origin(self, principal: McpPrincipal) -> dict[str, Any]:
        return {
            "source": self.source,
            "client_id": principal.client_id,
            "request_id": self.request_id,
            **({"rpc_id": self.rpc_id[:64]} if self.rpc_id else {}),
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; everything here is stored in UTC.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class McpAccessService:
    def __init__(
        self,
        repository: McpRepository,
        org: OrgService,
        grants: GrantService,
        datasource: DatasourceService,
        semantic: SemanticService,
        metric: MetricService,
        audit: AuditService,
    ) -> None:
        self.repository, self.org, self.grants = repository, org, grants
        self.datasource, self.semantic, self.metric, self.audit = (
            datasource,
            semantic,
            metric,
            audit,
        )
        # Request-scoped: one read of a connection's overrides per kind.
        self._overrides: dict[tuple[int, str], dict[int, Any]] = {}

    # --- administration ------------------------------------------------------------------

    async def _client(self, client_id: int) -> McpClient:
        row = await self.repository.clients.get(client_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row

    @staticmethod
    def principal_user(row: McpClient) -> CurrentUser:
        return CurrentUser(id=row.user_id, role=Role.MEMBER, team_id=None, client_id=row.id)

    async def _datasource_ids(self, row: McpClient) -> list[int]:
        """Datasources where the connection can see at least one column."""
        page = await self.datasource.choices(
            PageParams(page=1, page_size=100),
            self.grants.permissions,
            actor=self.principal_user(row),
        )
        return [item.id for item in page.items]

    async def _read(self, row: McpClient, *, detail: bool = False) -> McpClientRead:
        today = _now().date()
        stale = 0
        if detail:
            granted = set(await self.grants.client_column_ids(row.id))
            if granted:
                live: set[int] = set()
                for datasource_id in await self.datasource.active_ids():
                    catalog = await self.grants.schema.catalog(datasource_id)
                    live.update(c.id for t in catalog for c in t.columns)
                stale = len(granted - live)
        return McpClientRead(
            id=row.id,
            public_id=row.public_id,
            user_id=row.user_id,
            name=row.name,
            status="active" if row.status == "active" else "disabled",
            expires_at=_aware(row.expires_at),
            daily_call_limit=row.daily_call_limit,
            default_rows=row.default_rows,
            max_rows=row.max_rows,
            ask_timeout_seconds=row.ask_timeout_seconds,
            allow_run_sql=row.allow_run_sql,
            created_at=_aware(row.created_at) or _now(),
            last_used_at=_aware(row.last_used_at),
            calls_today=row.daily_calls if row.daily_date == today else 0,
            keys=[McpKeyRead.model_validate(key) for key in await self.repository.keys(row.id)],
            datasource_ids=await self._datasource_ids(row) if row.status == "active" else [],
            stale_grants=stale,
            override_count=len(await self.repository.overrides(row.id)),
        )

    async def clients(self, *, actor: CurrentUser) -> list[McpClientRead]:
        require_admin(actor)
        return [await self._read(row) for row in await self.repository.list_clients()]

    async def get(self, client_id: int, *, actor: CurrentUser) -> McpClientRead:
        require_admin(actor)
        return await self._read(await self._client(client_id), detail=True)

    async def create(
        self, data: McpClientWrite, *, actor: CurrentUser
    ) -> tuple[McpClientRead, McpKeyCreated]:
        """A connection starts with no grants and one key, shown once."""
        require_admin(actor)
        public_id = f"mc_{secrets.token_urlsafe(9)}"
        user_id = await self.org.create_service_account(f"mcp-{public_id}", actor=actor)
        row = await self.repository.clients.create(
            {**data.model_dump(), "public_id": public_id, "user_id": user_id, "status": "active"},
            actor.id,
        )
        key = await self._issue_key(row, actor)
        await self.audit.record(actor, "mcp.client.create", ("mcp_client", str(row.id)))
        return await self._read(row), key

    async def update(
        self, client_id: int, data: McpClientWrite, *, actor: CurrentUser
    ) -> McpClientRead:
        require_admin(actor)
        row = await self._client(client_id)
        await self.repository.clients.update(row, data.model_dump())
        await self.audit.record(
            actor, "mcp.client.update", ("mcp_client", str(client_id)), data.model_dump(mode="json")
        )
        return await self._read(row)

    async def set_status(
        self, client_id: int, active: bool, *, actor: CurrentUser
    ) -> McpClientRead:
        """Takes effect on the next call: every request re-reads the connection."""
        require_admin(actor)
        row = await self._client(client_id)
        row.status = "active" if active else "disabled"
        await self.org.set_service_status(row.user_id, active, actor=actor)
        await self.audit.record(
            actor,
            "mcp.client.enable" if active else "mcp.client.disable",
            ("mcp_client", str(row.id)),
        )
        return await self._read(row)

    async def delete(self, client_id: int, *, actor: CurrentUser) -> None:
        """Soft delete: keys revoked, grants, overrides and conversation ids dropped.

        The row and its service account stay so query logs and audit keep their subject.
        """
        require_admin(actor)
        row = await self._client(client_id)
        datasource_ids = await self._datasource_ids(row) if row.status == "active" else []
        now = _now()
        row.status, row.deleted_at = "disabled", now
        await self.org.set_service_status(row.user_id, False, actor=actor)
        await self.repository.revoke_keys(row.id, now)
        await self.repository.drop_overrides(row.id)
        await self.repository.drop_conversations(row.id)
        await self.grants.drop_client(row.id, datasource_ids)
        await self.audit.record(actor, "mcp.client.delete", ("mcp_client", str(row.id)))

    async def _issue_key(self, row: McpClient, actor: CurrentUser) -> McpKeyCreated:
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        stored = await self.repository.add_key(row.id, key[:10], hash_key(key), actor.id)
        return McpKeyCreated(key=key, detail=McpKeyRead.model_validate(stored))

    async def rotate_key(
        self, client_id: int, grace_hours: int, *, actor: CurrentUser
    ) -> McpKeyCreated:
        """A new key; the current ones stop working now, or after the grace period."""
        require_admin(actor)
        row = await self._client(client_id)
        now = _now()
        for key in await self.repository.keys(row.id):
            if key.revoked_at is not None:
                continue
            if grace_hours == 0:
                key.revoked_at = now
            else:
                ends = now + timedelta(hours=grace_hours)
                current = _aware(key.expires_at)
                key.expires_at = min(current, ends) if current else ends
        created = await self._issue_key(row, actor)
        await self.audit.record(
            actor, "mcp.key.rotate", ("mcp_client", str(row.id)), {"grace_hours": grace_hours}
        )
        return created

    async def revoke_key(self, client_id: int, key_id: int, *, actor: CurrentUser) -> McpKeyRead:
        require_admin(actor)
        await self._client(client_id)
        key = await self.repository.key(client_id, key_id)
        if key is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        key.revoked_at = key.revoked_at or _now()
        await self.audit.record(actor, "mcp.key.revoke", ("mcp_client", str(client_id)))
        return McpKeyRead.model_validate(key)

    async def read_grants(
        self, client_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> ClientGrantsRead:
        require_admin(actor)
        await self._client(client_id)
        return await self.grants.read_client(datasource_id, client_id, actor=actor)

    async def replace_grants(
        self, client_id: int, datasource_id: int, data: ClientGrantsWrite, *, actor: CurrentUser
    ) -> ClientGrantsRead:
        require_admin(actor)
        await self._client(client_id)
        result = await self.grants.replace_client(datasource_id, client_id, data, actor=actor)
        await self.audit.record(
            actor,
            "mcp.grants.replace",
            ("mcp_client", str(client_id)),
            {"datasource_id": datasource_id, "column_count": len(data.columns)},
        )
        return result

    # --- inherited terms and metrics -----------------------------------------------------

    async def overridden(self, client_id: int, kind: str) -> set[int]:
        """Ids of the platform items this connection has edited or hidden."""
        return set(await self._override_map(client_id, kind))

    async def _override_map(self, client_id: int, kind: str) -> dict[int, Any]:
        key = (client_id, kind)
        if key not in self._overrides:
            self._overrides[key] = {
                row.item_id: row for row in await self.repository.overrides(client_id, kind)
            }
        return self._overrides[key]

    async def semantics(
        self, user: CurrentUser, datasource_id: int, items: list[SemanticView]
    ) -> list[SemanticView]:
        if user.client_id is None:
            return items
        overrides = await self._override_map(user.client_id, "semantic")
        return [
            item.model_copy(update=overrides[item.id].payload) if item.id in overrides else item
            for item in items
            if item.id not in overrides or overrides[item.id].action != "hide"
        ]

    async def metrics(
        self, user: CurrentUser, datasource_id: int, items: list[MetricView]
    ) -> list[MetricView]:
        if user.client_id is None:
            return items
        overrides = await self._override_map(user.client_id, "metric")
        return [
            item.model_copy(update=overrides[item.id].payload) if item.id in overrides else item
            for item in items
            if item.id not in overrides or overrides[item.id].action != "hide"
        ]

    async def overlay_items(
        self, client_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> list[OverlayItem]:
        """Every platform term and metric the connection inherits on one datasource.

        Overrides of items it cannot see (deleted, or reading columns it is not granted) are
        kept but not listed: they take effect again if the item becomes visible.
        """
        require_admin(actor)
        row = await self._client(client_id)
        user = self.principal_user(row)
        semantic = await self._override_map(row.id, "semantic")
        metric = await self._override_map(row.id, "metric")
        result = []
        for term in await self.semantic.list(user, datasource_id):
            if term.scope != "platform":
                continue
            base = term.model_dump(mode="json", include=set(SEMANTIC_FIELDS))
            result.append(self._overlay("semantic", term.id, self._term_name(term), base, semantic))
        for item in await self.metric.list(user, datasource_id):
            if item.scope != "platform":
                continue
            base = item.model_dump(mode="json", include=set(METRIC_FIELDS))
            result.append(self._overlay("metric", item.id, item.name, base, metric))
        return result

    @staticmethod
    def _term_name(term: SemanticView) -> str:
        return term.aliases[0] if term.aliases else f"{term.target_type}#{term.target_id}"

    @staticmethod
    def _overlay(
        kind: Literal["semantic", "metric"],
        item_id: int,
        name: str,
        base: dict[str, Any],
        overrides: dict[int, Any],
    ) -> OverlayItem:
        row = overrides.get(item_id)
        state: Literal["default", "edited", "hidden"] = (
            "default" if row is None else "hidden" if row.action == "hide" else "edited"
        )
        effective = {**base, **row.payload} if row is not None and row.action != "hide" else base
        return OverlayItem(
            kind=kind, item_id=item_id, name=name, platform=base, effective=effective, state=state
        )

    async def set_override(
        self, client_id: int, datasource_id: int, data: OverrideWrite, *, actor: CurrentUser
    ) -> OverlayItem:
        require_admin(actor)
        row = await self._client(client_id)
        items = {
            (item.kind, item.item_id): item
            for item in await self.overlay_items(client_id, datasource_id, actor=actor)
        }
        if (data.kind, data.item_id) not in items:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if (
            data.kind == "metric"
            and data.action == "edit"
            and ("sql_template" in data.payload or "params" in data.payload)
        ):
            # An edited definition must stay inside what this connection may read.
            current = items[(data.kind, data.item_id)].effective
            template = data.payload.get("sql_template", current["sql_template"])
            params = data.payload.get("params", current["params"])
            user = self.principal_user(row)
            scope = await self.grants.permissions.visible_scope(user, datasource_id)
            source = await self.metric.datasources.resolve(datasource_id, user)
            self.metric.guard.check(render_template(template, params), source.dialect, scope)
        await self.repository.set_override(
            row.id, data.kind, data.item_id, data.action, data.payload, actor.id
        )
        self._overrides.pop((row.id, data.kind), None)
        await self.audit.record(
            actor,
            f"mcp.override.{data.action}",
            ("mcp_client", str(row.id)),
            {"kind": data.kind, "item_id": data.item_id},
        )
        refreshed = await self.overlay_items(client_id, datasource_id, actor=actor)
        return next(i for i in refreshed if (i.kind, i.item_id) == (data.kind, data.item_id))

    async def restore(self, client_id: int, data: OverrideRestore, *, actor: CurrentUser) -> int:
        """Back to the platform default: one item, one kind, or every override."""
        require_admin(actor)
        row = await self._client(client_id)
        if data.item_id is not None and data.kind is None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        removed = await self.repository.drop_overrides(
            row.id, data.kind, [data.item_id] if data.item_id is not None else None
        )
        self._overrides.clear()
        await self.audit.record(
            actor,
            "mcp.override.restore",
            ("mcp_client", str(row.id)),
            {"kind": data.kind, "item_id": data.item_id, "removed": removed},
        )
        return removed

    # --- runtime -------------------------------------------------------------------------

    async def authenticate(self, key: str) -> McpPrincipal:
        """The only way to a principal: a live key of a live connection."""
        if not key.startswith(KEY_PREFIX) or len(key) > 200:
            raise AppError(ErrorCode.AUTH_FAILED, http_status=401)
        stored = await self.repository.key_by_hash(hash_key(key))
        now = _now()
        if stored is None or stored.revoked_at is not None:
            raise AppError(ErrorCode.AUTH_FAILED, http_status=401)
        expires = _aware(stored.expires_at)
        if expires is not None and expires <= now:
            raise AppError(ErrorCode.AUTH_FAILED, http_status=401)
        row = await self.repository.clients.get(stored.client_id)
        if row is None or row.status != "active":
            raise AppError(ErrorCode.FORBIDDEN, http_status=403)
        client_expires = _aware(row.expires_at)
        if client_expires is not None and client_expires <= now:
            raise AppError(ErrorCode.FORBIDDEN, http_status=403)
        stored.last_used_at = row.last_used_at = now
        await self.repository.session.flush()
        return self.principal(row)

    def principal(self, row: McpClient) -> McpPrincipal:
        return McpPrincipal(
            user=self.principal_user(row),
            client_id=row.id,
            public_id=row.public_id,
            name=row.name,
            default_rows=row.default_rows,
            max_rows=row.max_rows,
            ask_timeout_seconds=row.ask_timeout_seconds,
            allow_run_sql=row.allow_run_sql,
        )

    async def test_principal(self, client_id: int, *, actor: CurrentUser) -> McpPrincipal:
        """An admin testing a connection acts as it, whatever its status or keys."""
        require_admin(actor)
        return self.principal(await self._client(client_id))

    async def admit(self, principal: McpPrincipal) -> None:
        if not await self.repository.admit(principal.client_id, _now().date()):
            raise AppError(ErrorCode.QUOTA_EXCEEDED, http_status=429)
        await self.repository.commit()

    async def visible_datasource(self, principal: McpPrincipal, datasource_id: int) -> bool:
        try:
            scope = await self.grants.permissions.visible_scope(principal.user, datasource_id)
        except AppError as error:
            if error.code in {
                ErrorCode.NOT_FOUND,
                ErrorCode.FORBIDDEN,
                ErrorCode.DATASOURCE_UNAVAILABLE,
            }:
                return False
            raise
        return bool(scope.tables)

    async def conversation(self, principal: McpPrincipal, public_id: str) -> Any:
        """A conversation this connection started. Another connection's id, an unknown id
        and an expired one are indistinguishable."""
        row = await self.repository.conversation(principal.client_id, public_id)
        used = _aware(row.last_used_at) if row else None
        if row is None or used is None or used < _now() - CONVERSATION_TTL:
            raise AppError(ErrorCode.NOT_FOUND, "conversation not found", http_status=404)
        return row

    async def bind_conversation(
        self, principal: McpPrincipal, conversation_id: int, datasource_id: int
    ) -> Any:
        return await self.repository.add_conversation(
            principal.client_id,
            f"conv_{secrets.token_urlsafe(16)}",
            conversation_id,
            datasource_id,
            _now(),
        )

    @staticmethod
    def touch(row: Any, pending_message_id: int | None) -> None:
        row.last_used_at = _now()
        row.pending_message_id = pending_message_id


# --- the agent's view ----------------------------------------------------------------------

# Safe, fixed messages per failure. They name no table, column or value.
AGENT_MESSAGES = {
    "SQL_OUT_OF_SCOPE": "当前接入无权查询这些数据",
    "SQL_NOT_SELECT": "只允许只读查询",
    "SQL_MULTI_STATEMENT": "一次只能执行一条查询",
    "SQL_FORBIDDEN_FUNC": "查询使用了不允许的函数",
    "SQL_SYSTEM_TABLE": "不允许查询系统表",
    "SQL_PARSE_FAILED": "SQL 无法解析",
    "SQL_COST_EXCEEDED": "查询代价过高，请缩小范围",
    "SQL_EXECUTION_ERROR": "SQL 执行出错，请检查语法和字段",
    "QUERY_TIMEOUT": "查询超时，请缩小范围后重试",
    "TIMEOUT": "问数超时，请稍后重试或把问题拆小",
    "QUERY_CANCELLED": "查询已取消",
    "RATE_LIMITED": "请求过于频繁，请稍后重试",
    "QUOTA_EXCEEDED": "今日调用次数已用完",
    "LLM_UNAVAILABLE": "模型服务暂不可用，请稍后重试",
    "DATASOURCE_UNAVAILABLE": "数据源不存在或当前接入不可用",
    "CONVERSATION_DATASOURCE_MISMATCH": "这个会话属于另一个数据源，请去掉 conversation_id 新开会话",
    "RUN_SQL_DISABLED": "当前接入未开启直接执行 SQL，请改用 ask",
    "TABLE_NOT_FOUND": "表不存在或当前接入不可见",
    "VALIDATION_ERROR": "模型输出无效，请换个说法重试",
}
RETRYABLE = {"QUERY_TIMEOUT", "TIMEOUT", "RATE_LIMITED", "LLM_UNAVAILABLE", "INTERNAL_ERROR"}


def agent_error(code: str, ref: CallRef) -> AgentError:
    message = AGENT_MESSAGES.get(code)
    if message is None:
        known = code in ErrorCode.__members__
        message = MESSAGES[ErrorCode(code)] if known else "查询失败"
    return AgentError(
        code=code, message=message, retryable=code in RETRYABLE, request_id=ref.request_id
    )


def dataset(
    columns: list[str], rows: list[dict[str, Any]], row_count: int, cut: bool, limit: int
) -> AgentDataset:
    """At most `limit` rows and CHAR_BUDGET characters of them: what fits an agent's context."""
    kept: list[dict[str, Any]] = []
    size = 0
    by: Literal["rows", "chars", "database"] | None = None
    for row in plain_rows(rows[:limit]):
        size += len(json.dumps(row, ensure_ascii=False, default=str))
        if size > CHAR_BUDGET and kept:
            by = "chars"
            break
        kept.append(row)
    if by is None and len(rows) > len(kept):
        by = "rows"
    if by is None and cut:
        by = "database"
    return AgentDataset(
        columns=columns,
        rows=kept,
        row_count=row_count,
        returned_rows=len(kept),
        truncated=by is not None,
        truncated_by=by,
    )


Progress = Callable[[float, str], Awaitable[None]]
STAGE_PROGRESS = {
    "visible_scope": (1, "检查权限"),
    "retrieval": (2, "检索相关表和指标"),
    "generate_sql": (3, "生成 SQL"),
    "sql_guard": (4, "安全检查"),
    "execute": (5, "执行查询"),
    "presentation": (6, "整理结论"),
}


@dataclass
class AgentOutcome:
    """A tool result, plus the run behind it for the admin's test view."""

    result: AskResult | SqlResult | AgentError
    context: Any = None

    @property
    def is_error(self) -> bool:
        return isinstance(self.result, AgentError)


class AgentService:
    """What MCP tools do, as the connection's service account and nothing more."""

    def __init__(self, access: McpAccessService, chat: ChatService) -> None:
        self.access, self.chat = access, chat

    async def ask(
        self,
        principal: McpPrincipal,
        question: str,
        datasource_id: int,
        ref: CallRef,
        *,
        conversation_id: str | None = None,
        choice: str | None = None,
        max_rows: int | None = None,
        progress: Progress | None = None,
    ) -> AgentOutcome:
        if conversation_id is not None:
            # Raised: an unknown id is a malformed call, not something to answer.
            binding = await self.access.conversation(principal, conversation_id)
            if binding.datasource_id != datasource_id:
                return AgentOutcome(agent_error("CONVERSATION_DATASOURCE_MISMATCH", ref))
        if not await self.access.visible_datasource(principal, datasource_id):
            return AgentOutcome(agent_error("DATASOURCE_UNAVAILABLE", ref))
        try:
            await self.access.admit(principal)
        except AppError as error:
            return AgentOutcome(agent_error(error.code.value, ref))
        if conversation_id is None:
            created = await self.chat.create(
                ConversationCreate(datasource_id=datasource_id), actor=principal.user
            )
            binding = await self.access.bind_conversation(principal, created.id, datasource_id)
        pending = binding.pending_message_id if choice else None
        request = QuestionRequest(
            question=question,
            clarification_message_id=pending,
            clarification_choice=choice if pending else None,
        )
        ctx = await self.chat.prepare(binding.conversation_id, request, actor=principal.user)
        ctx.origin = ref.origin(principal)

        reported = 0.0

        async def emit(kind: EventType, data: dict[str, Any]) -> None:
            nonlocal reported
            stage = STAGE_PROGRESS.get(str(data.get("name"))) if kind == EventType.STAGE else None
            # MCP requires progress to increase: a stage that does not move forward is skipped.
            if progress is not None and stage is not None and stage[0] > reported:
                reported = stage[0]
                await progress(stage[0], stage[1])

        started = time.monotonic()
        try:
            async with asyncio.timeout(principal.ask_timeout_seconds) as deadline:
                reply = await self.chat.run_question(ctx, emit)
        except AppError as error:
            if error.code != ErrorCode.QUERY_CANCELLED:
                raise
            # The pipeline turns cancellation into this error: by our deadline or the client.
            code = "TIMEOUT" if deadline.expired() else "QUERY_CANCELLED"
            return AgentOutcome(agent_error(code, ref), ctx)
        except TimeoutError:
            return AgentOutcome(agent_error("TIMEOUT", ref), ctx)
        self.access.touch(binding, reply.message_id if reply.status == "clarify" else None)
        await self.access.repository.commit()
        usage = AgentUsage(
            input_tokens=ctx.tokens_in,
            output_tokens=ctx.tokens_out,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        if reply.status in {"blocked", "failed"}:
            return AgentOutcome(agent_error(reply.code.value, ref), ctx)
        edited = await self.access.overridden(principal.client_id, "metric")
        ids = {item.name: item.ref_id for item in ctx.retrieval.items if item.kind == "metric"}
        used = [
            AgentMetric(
                name=item.name,
                source="connection" if ids.get(item.name) in edited else "platform",
            )
            for item in (ctx.meta.definitions if ctx.meta else [])
            if item.kind == "metric"
        ]
        base = {
            "request_id": ref.request_id,
            "conversation_id": binding.public_id,
            "metrics_used": used,
            "usage": usage,
        }
        if reply.status == "clarify" and reply.clarification is not None:
            return AgentOutcome(
                AskResult(
                    status="clarify",
                    question=reply.clarification.question,
                    options=reply.clarification.choices,
                    **base,
                ),
                ctx,
            )
        result = ctx.result
        limit = min(max_rows or principal.default_rows, principal.max_rows)
        data = (
            dataset(result.columns, result.rows, result.row_count, result.truncated, limit)
            if result is not None
            else None
        )
        return AgentOutcome(
            AskResult(
                status="empty" if data is None or data.row_count == 0 else "success",
                answer=reply.explanation,
                sql=reply.sql,
                data=data,
                assumption=ctx.assumption,
                **base,
            ),
            ctx,
        )

    async def run_sql(
        self,
        principal: McpPrincipal,
        sql: str,
        datasource_id: int,
        ref: CallRef,
        *,
        max_rows: int | None = None,
    ) -> AgentOutcome:
        if not principal.allow_run_sql:
            return AgentOutcome(agent_error("RUN_SQL_DISABLED", ref))
        if not await self.access.visible_datasource(principal, datasource_id):
            return AgentOutcome(agent_error("DATASOURCE_UNAVAILABLE", ref))
        try:
            await self.access.admit(principal)
        except AppError as error:
            return AgentOutcome(agent_error(error.code.value, ref))
        started = time.monotonic()
        ctx = await self.chat.execute_sql(
            datasource_id, sql, actor=principal.user, origin=ref.origin(principal)
        )
        if ctx.error_code is not None or ctx.result is None or ctx.guarded_sql is None:
            return AgentOutcome(
                agent_error((ctx.error_code or ErrorCode.INTERNAL_ERROR).value, ref), ctx
            )
        limit = min(max_rows or principal.default_rows, principal.max_rows)
        data = dataset(
            ctx.result.columns, ctx.result.rows, ctx.result.row_count, ctx.result.truncated, limit
        )
        return AgentOutcome(
            SqlResult(
                status="empty" if data.row_count == 0 else "success",
                request_id=ref.request_id,
                sql=ctx.guarded_sql.normalized_sql,
                data=data,
                usage=AgentUsage(duration_ms=int((time.monotonic() - started) * 1000)),
            ),
            ctx,
        )

    async def test(
        self, client_id: int, request: TestAskRequest, *, actor: CurrentUser
    ) -> TestAskReply:
        """Ask as the connection, with its grants, overrides and limits, for an admin."""
        principal = await self.access.test_principal(client_id, actor=actor)
        ref = CallRef(source="mcp_test")
        try:
            outcome = await self.ask(
                principal,
                request.question,
                request.datasource_id,
                ref,
                conversation_id=request.conversation_id,
            )
        except AppError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
            outcome = AgentOutcome(
                AgentError(code="INVALID_PARAMS", message=error.message, request_id=ref.request_id)
            )
        return TestAskReply(
            agent=outcome.result.model_dump(mode="json"),
            is_error=outcome.is_error,
            diagnostics=test_diagnostics(principal, request.datasource_id, outcome.context),
        )

    # --- read-only catalog tools: no model call, no daily count ---------------------------

    async def datasources(self, principal: McpPrincipal) -> list[dict[str, Any]]:
        page = await self.access.datasource.choices(
            PageParams(page=1, page_size=100),
            self.access.grants.permissions,
            actor=principal.user,
        )
        return [
            {"datasource_id": item.id, "name": item.name, "dialect": str(item.dialect)}
            for item in page.items
        ]

    async def _tables(self, principal: McpPrincipal, datasource_id: int) -> list[Any] | None:
        if not await self.access.visible_datasource(principal, datasource_id):
            return None
        return await self.access.grants.schema.browse(
            datasource_id, self.access.grants.permissions, actor=principal.user
        )

    async def tables(
        self, principal: McpPrincipal, datasource_id: int, ref: CallRef
    ) -> list[dict[str, Any]] | AgentError:
        tables = await self._tables(principal, datasource_id)
        if tables is None:
            return agent_error("DATASOURCE_UNAVAILABLE", ref)
        return [
            {
                "table": table.qualified_name,
                "description": table.description or table.comment,
                "columns": len(table.columns),
            }
            for table in tables
        ]

    async def describe(
        self, principal: McpPrincipal, datasource_id: int, table: str, ref: CallRef
    ) -> dict[str, Any] | AgentError:
        tables = await self._tables(principal, datasource_id)
        if tables is None:
            return agent_error("DATASOURCE_UNAVAILABLE", ref)
        found = next((t for t in tables if table in {t.qualified_name, t.name}), None)
        if found is None:
            # The same answer whether the table is missing or withheld.
            return agent_error("TABLE_NOT_FOUND", ref)
        scope = await self.access.grants.permissions.visible_scope(principal.user, datasource_id)
        masks = scope.tables[found.qualified_name].masks
        index = self.chat.pipeline.deps.builder.index
        enums: dict[str, Any] = {}
        semantics = getattr(index, "semantics", None)
        if semantics is not None:
            for term in await semantics(principal.user, datasource_id):
                if term.target_type != "enum" or not term.enum_mapping:
                    continue
                target = await self.access.semantic.targets.resolve_target(
                    term.datasource_id, term.target_type, term.target_id
                )
                if target.table == found.qualified_name and target.column:
                    enums[target.column] = term.enum_mapping
        return {
            "table": found.qualified_name,
            "description": found.description or found.comment,
            "columns": [
                {
                    "name": column.name,
                    "type": column.data_type,
                    "description": column.description or column.comment,
                    **({"primary_key": True} if column.is_pk else {}),
                    **({"masked": True} if column.name in masks else {}),
                    **({"values": enums[column.name]} if column.name in enums else {}),
                }
                for column in found.columns
            ],
        }

    async def search(
        self, principal: McpPrincipal, datasource_id: int, query: str, ref: CallRef
    ) -> dict[str, Any] | AgentError:
        """The same hybrid retrieval ask uses; keyword-only without an embedding model."""
        if not await self.access.visible_datasource(principal, datasource_id):
            return agent_error("DATASOURCE_UNAVAILABLE", ref)
        scope = await self.access.grants.permissions.visible_scope(principal.user, datasource_id)
        bundle = await self.chat.pipeline.deps.builder.build(principal.user, scope, query)
        return {
            "mode": bundle.mode,
            "tables": {name: sorted(columns) for name, columns in bundle.tables.items()},
            "items": [
                {"kind": item.kind, "name": item.name, "description": item.description}
                for item in bundle.items
            ],
        }

    async def definitions(
        self, principal: McpPrincipal, datasource_id: int, kind: str, ref: CallRef
    ) -> list[dict[str, Any]] | AgentError:
        if not await self.access.visible_datasource(principal, datasource_id):
            return agent_error("DATASOURCE_UNAVAILABLE", ref)
        index = self.chat.pipeline.deps.builder.index
        overrides = await self.access.overridden(principal.client_id, kind)
        if kind == "metric":
            metrics = await index.metrics(principal.user, datasource_id)  # type: ignore[attr-defined]
            return [
                {
                    "name": item.name,
                    "aliases": item.aliases,
                    "description": item.description,
                    "source": "connection" if item.id in overrides else "platform",
                }
                for item in metrics
            ]
        terms = await index.semantics(principal.user, datasource_id)  # type: ignore[attr-defined]
        return [
            {
                "aliases": item.aliases,
                "description": item.description,
                **({"values": item.enum_mapping} if item.enum_mapping else {}),
                "source": "connection" if item.id in overrides else "platform",
            }
            for item in terms
        ]


def test_diagnostics(principal: McpPrincipal, datasource_id: int, ctx: Any) -> TestDiagnostics:
    """The admin-only side of a test call: what the connection saw and where it stopped."""
    if ctx is None:
        return TestDiagnostics(principal=f"MCP / {principal.name}", datasource_id=datasource_id)
    diagnostic = ctx.diagnostic or {}
    rule = diagnostic.get("rule")
    guard: Literal["passed", "blocked", "not_reached"] = (
        "blocked"
        if ctx.status == "blocked"
        else "passed"
        if ctx.guarded_sql is not None
        else "not_reached"
    )
    scope = ctx.scope
    return TestDiagnostics(
        principal=f"MCP / {principal.name}",
        datasource_id=datasource_id,
        tables=sorted(scope.tables) if scope else [],
        columns={name: sorted(columns) for name, columns in ctx.retrieval.tables.items()},
        items=[
            {"kind": item.kind, "name": item.name, "scope": item.scope}
            for item in ctx.retrieval.items
        ],
        guard=guard,
        rule=rule,
        masked=sorted(ctx.guarded_sql.output_masks) if ctx.guarded_sql else [],
        stage=str(diagnostic.get("stage") or ctx.stage or ""),
        timings=dict(ctx.timings),
        input_tokens=ctx.tokens_in,
        output_tokens=ctx.tokens_out,
        query_run_id=ctx.query_run_id,
    )


def build_service(
    session: Any,
    org: OrgService,
    grants: GrantService,
    datasource: DatasourceService,
    semantic: SemanticService,
    metric: MetricService,
    audit: AuditService,
) -> McpAccessService:
    return McpAccessService(
        McpRepository(session), org, grants, datasource, semantic, metric, audit
    )
