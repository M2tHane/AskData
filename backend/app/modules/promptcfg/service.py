from typing import Annotated

from fastapi import Depends, Request

from app.core.audit import AuditService
from app.core.db import AsyncSession
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.prompt import (
    DIALECT_NAMES,
    ActiveRevision,
    PromptId,
    PromptOverrides,
    PromptRegistry,
    dialect_rules,
    validate_dialect_text,
    validate_template,
)
from app.modules.auth.service import audit_for, get_session, require_admin
from app.modules.org.service import OrgService
from app.modules.org.service import build_service as build_org
from app.modules.promptcfg.models import PromptRevision
from app.modules.promptcfg.repository import PromptRepository
from app.modules.promptcfg.schemas import (
    PromptDetail,
    PromptKind,
    PromptSummary,
    RevisionMeta,
    RevisionRead,
    RevisionWrite,
)

LABELS: dict[str, tuple[str, str]] = {
    PromptId.SQL.value: (
        "生成 SQL",
        "一次调用生成只读 SELECT，并给出澄清、推荐图表、标题和问题理解；输出 JSON。",
    ),
    PromptId.CORRECT.value: ("SQL 自动纠错", "执行报错后让模型修正 SQL，最多重试三次。"),
    PromptId.ANSWER.value: ("生成回答", "根据查询结果写 1～3 句结论，流式返回给用户。"),
    PromptId.SUGGEST.value: ("猜你想问", "根据可见范围生成示例问题。"),
    PromptId.DESCRIBE.value: (
        "AI 补全字段语义",
        "接入数据源时，根据表名、字段名和类型为缺少注释的字段起草语义，管理员确认后保存。",
    ),
    "mysql": (
        "MySQL 方言",
        "MySQL 的引号、日期、字符串、类型转换等书写规则，拼进生成 SQL 的提示词。",
    ),
    "postgresql": ("PostgreSQL 方言", "PostgreSQL 的书写规则，拼进生成 SQL 的提示词。"),
    "kingbase": (
        "KingbaseES 方言",
        "人大金仓（KingbaseES）是 PostgreSQL 兼容的国产数据库；使用 PostgreSQL 语法。",
    ),
    "gaussdb": (
        "GaussDB 方言",
        "GaussDB/openGauss 是 PostgreSQL 兼容的国产数据库；使用 PostgreSQL 语法。",
    ),
    "clickhouse": (
        "ClickHouse 方言",
        "ClickHouse 是列式 OLAP 数据库；不同的引号、日期函数、聚合函数和约束。",
    ),
}


class PromptConfigService:
    """Admins publish text for prompts that core already registers; ids and variables stay fixed."""

    def __init__(
        self, repository: PromptRepository, audit: AuditService, org: OrgService | None = None
    ) -> None:
        self.repository, self.audit, self.org = repository, audit, org
        self.registry = PromptRegistry()

    # ---- runtime (no actor): the pipeline asks for what is currently published ----
    async def load(self) -> PromptOverrides:
        overrides = PromptOverrides()
        for row in await self.repository.active():
            published = ActiveRevision(label=f"r{row.revision}", content=row.content)
            if row.kind == "system" and row.key in {p.value for p in PromptId}:
                overrides.system[row.key] = published
            elif row.kind == "dialect" and row.key in DIALECT_NAMES:
                overrides.dialects[row.key] = published
        return overrides

    # ---- helpers ----
    def _seed(self, kind: PromptKind, key: str) -> tuple[str, list[str], str]:
        if kind == "system" and key in {p.value for p in PromptId}:
            return self.registry.seed(PromptId(key))
        if kind == "dialect" and key in DIALECT_NAMES:
            return "v1", [], dialect_rules(key)
        raise AppError(ErrorCode.NOT_FOUND, http_status=404)

    def _validate(self, kind: PromptKind, key: str, content: str) -> None:
        if kind == "system":
            validate_template(self._seed(kind, key)[1], content)
        else:
            validate_dialect_text(content)

    async def _authors(self, rows: list[PromptRevision]) -> dict[int, str]:
        names: dict[int, str] = {}
        if self.org is None:
            return names
        for identifier in {row.created_by for row in rows if row.created_by}:
            try:
                names[identifier] = (await self.org.user(identifier)).username
            except AppError:
                names[identifier] = ""
        return names

    @staticmethod
    def _meta(row: PromptRevision, authors: dict[int, str]) -> RevisionMeta:
        return RevisionMeta(
            id=row.id,
            revision=row.revision,
            note=row.note,
            is_active=row.is_active,
            author=authors.get(row.created_by or 0, ""),
            created_at=row.created_at,
        )

    # ---- admin API ----
    async def overview(self, *, actor: CurrentUser) -> list[PromptSummary]:
        require_admin(actor)
        out: list[PromptSummary] = []
        keys: list[tuple[PromptKind, str]] = [("system", p.value) for p in PromptId] + [
            ("dialect", name) for name in DIALECT_NAMES
        ]
        for kind, key in keys:
            version, variables, _ = self._seed(kind, key)
            rows = await self.repository.revisions(kind, key)
            active = next((row for row in rows if row.is_active), None)
            label, description = LABELS[key]
            out.append(
                PromptSummary(
                    kind=kind,
                    key=key,
                    label=label,
                    description=description,
                    seed_version=version,
                    variables=variables,
                    active_label=f"r{active.revision}" if active else f"{version}（内置）",
                    revision_count=len(rows),
                    updated_at=rows[0].created_at if rows else None,
                )
            )
        return out

    async def detail(self, kind: PromptKind, key: str, *, actor: CurrentUser) -> PromptDetail:
        require_admin(actor)
        version, variables, seed = self._seed(kind, key)
        rows = await self.repository.revisions(kind, key)
        active = next((row for row in rows if row.is_active), None)
        authors = await self._authors(rows)
        label, description = LABELS[key]
        return PromptDetail(
            kind=kind,
            key=key,
            label=label,
            description=description,
            seed_version=version,
            variables=variables,
            seed_content=seed,
            active_label=f"r{active.revision}" if active else f"{version}（内置）",
            active_content=active.content if active else seed,
            revisions=[self._meta(row, authors) for row in rows],
        )

    async def revision(
        self, kind: PromptKind, key: str, identifier: int, *, actor: CurrentUser
    ) -> RevisionRead:
        require_admin(actor)
        self._seed(kind, key)
        row = await self.repository.revision(kind, key, identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        meta = self._meta(row, await self._authors([row]))
        return RevisionRead(**meta.model_dump(), content=row.content)

    async def create(
        self, kind: PromptKind, key: str, data: RevisionWrite, *, actor: CurrentUser
    ) -> RevisionRead:
        require_admin(actor)
        self._seed(kind, key)
        self._validate(kind, key, data.content)
        if data.activate:
            await self.repository.deactivate(kind, key)
        row = await self.repository.create(
            {
                "kind": kind,
                "key": key,
                "revision": await self.repository.next_revision(kind, key),
                "content": data.content.strip(),
                "note": data.note,
                "is_active": data.activate,
            },
            actor.id,
        )
        await self.audit.record(
            actor,
            "prompt.create",
            ("prompt", f"{kind}:{key}"),
            {"revision": row.revision, "active": data.activate},
        )
        meta = self._meta(row, await self._authors([row]))
        return RevisionRead(**meta.model_dump(), content=row.content)

    async def activate(
        self, kind: PromptKind, key: str, identifier: int, *, actor: CurrentUser
    ) -> None:
        require_admin(actor)
        self._seed(kind, key)
        row = await self.repository.revision(kind, key, identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        # Older text was validated when it was published, but registered variables may have moved.
        self._validate(kind, key, row.content)
        await self.repository.deactivate(kind, key)
        row.is_active = True
        await self.repository.session.flush()
        await self.audit.record(
            actor, "prompt.activate", ("prompt", f"{kind}:{key}"), {"revision": row.revision}
        )

    async def reset(self, kind: PromptKind, key: str, *, actor: CurrentUser) -> None:
        require_admin(actor)
        self._seed(kind, key)
        await self.repository.deactivate(kind, key)
        await self.audit.record(actor, "prompt.reset", ("prompt", f"{kind}:{key}"))


def build_service(session: AsyncSession) -> PromptConfigService:
    return PromptConfigService(PromptRepository(session), audit_for(session), build_org(session))


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session, scope="function")]
) -> PromptConfigService:
    return build_service(session)
