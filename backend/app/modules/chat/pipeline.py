import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.audit import QueryLogWriter
from app.core.datasource import Datasource
from app.core.deps import CurrentUser
from app.core.errors import MESSAGES, AppError, ErrorCode, SqlBlocked
from app.core.llm import CallContext, ChatMessage, LLMClient
from app.core.permission import PermissionService, TableScope, VisibleScope
from app.core.prompt import PromptId, PromptRegistry
from app.core.scope import ScopedQuery
from app.core.sql.dialect import DialectAdapter
from app.core.sql.executor import QueryLimits, QueryResult, SqlExecutor
from app.core.sql.guard import GuardedSql, SqlGuard
from app.core.stream import EventType, StreamEvent
from app.modules.chat.schemas import (
    ChartSpec,
    Clarification,
    Explanation,
    MessageView,
    QueryReply,
    RetrievalBundle,
)


class RetrievalIndex(Protocol):
    """B-group service adapter: pre-filter every kind by user and scope before retrieval.

    hybrid means keyword + embedding, optionally reranked. expand_relations must use
    only authorized foreign-key endpoints. No repository or B-group imports are needed.
    """

    async def retrieve(
        self, user: CurrentUser, scope: VisibleScope, question: str, mode: str
    ) -> RetrievalBundle: ...

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle: ...


class ScopeOnlyIndex:
    """Safe bootstrap adapter; hybrid deployments must inject a real index service."""

    async def retrieve(
        self, user: CurrentUser, scope: VisibleScope, question: str, mode: str
    ) -> RetrievalBundle:
        if mode == "hybrid":
            raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
        return RetrievalBundle(
            tables={
                name: {column: table.all_columns[column] for column in sorted(table.columns)}
                for name, table in scope.tables.items()
            }
        )

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle:
        return bundle


class ContextBuilder:
    def __init__(self, index: RetrievalIndex, threshold: int = 30) -> None:
        self.index, self.threshold = index, threshold

    async def build(
        self, user: CurrentUser, scope: VisibleScope, question: str, choice: str | None = None
    ) -> RetrievalBundle:
        # Do not give the index withheld column metadata retained by the SQL guard.
        safe_scope = VisibleScope(
            datasource_id=scope.datasource_id,
            tables={
                name: TableScope(
                    all_columns={column: table.all_columns[column] for column in table.columns},
                    columns=table.columns,
                )
                for name, table in scope.tables.items()
            },
        )
        mode: Literal["full", "hybrid"] = (
            "full" if len(safe_scope.tables) <= self.threshold else "hybrid"
        )
        bundle = await self.index.retrieve(user, safe_scope, question, mode)
        bundle = await self.index.expand_relations(user, safe_scope, bundle)
        tables = {
            name: {
                column: scope.tables[name].all_columns[column]
                for column in columns
                if column in scope.tables[name].columns
            }
            for name, columns in bundle.tables.items()
            if name in scope.tables
        }
        if mode == "full":
            tables = {name: dict(table.all_columns) for name, table in safe_scope.tables.items()}
        authorized = [
            item
            for item in bundle.items
            if item.tables
            and all(
                name in scope.tables and columns <= scope.tables[name].columns
                for name, columns in item.tables.items()
            )
        ]
        visible_items = ScopedQuery.visible_to(authorized, user)
        merged = ScopedQuery.merge(visible_items, user, "key")
        ambiguities = list(bundle.ambiguities)
        # Same term with different definitions remains explicit despite scope precedence.
        for key in dict.fromkeys(item.key for item in visible_items):
            matches = [item for item in visible_items if item.key == key and item.score > 0]
            if len({item.definition for item in matches}) > 1:
                ambiguities.append(
                    Clarification(
                        question="请选择指标或语义口径",
                        choices=[
                            f"{item.name}（{item.scope.value}级）：{item.definition}"
                            for item in matches
                        ][:8],
                    )
                )
        metrics = sorted(
            (item for item in merged if item.kind == "metric"),
            key=lambda item: item.score,
            reverse=True,
        )
        if (
            len(metrics) >= 2
            and metrics[0].score > 0
            and abs(metrics[0].score - metrics[1].score) <= 0.05
        ):
            ambiguities.append(
                Clarification(question="请选择指标", choices=[item.name for item in metrics[:8]])
            )
        if choice:
            selected = [
                item
                for item in visible_items
                if choice in {item.name, f"{item.name}（{item.scope.value}级）：{item.definition}"}
            ]
            if len(selected) == 1:
                chosen = selected[0]
                merged = [
                    item
                    for item in merged
                    if item.key != chosen.key and (chosen.kind != "metric" or item.kind != "metric")
                ]
                merged.append(chosen)
        # Example SQL is still only prompt data; every resulting SQL passes the guard.
        return RetrievalBundle(
            tables=tables,
            items=merged,
            required_conditions=bundle.required_conditions,
            ambiguities=ambiguities,
            mode=mode,
        )


def resolve_time_ranges(question: str, timezone: str, now: datetime) -> dict[str, Any]:
    today = now.astimezone(ZoneInfo(timezone)).date()
    ranges: dict[str, Any] = {"timezone": timezone, "today": today.isoformat()}
    tomorrow = today + timedelta(days=1)
    if "上个月" in question:
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        ranges["上个月"] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    for match in re.finditer(r"(?:近|最近)\s*(\d{1,3})\s*天", question):
        days = int(match[1])
        if days < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        ranges[match[0]] = {
            "start": (tomorrow - timedelta(days=days)).isoformat(),
            "end_exclusive": tomorrow.isoformat(),
        }
    if "本季度" in question:
        month = ((today.month - 1) // 3) * 3 + 1
        start = date(today.year, month, 1)
        end = date(today.year + 1, 1, 1) if month == 10 else date(today.year, month + 3, 1)
        ranges["本季度"] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    if "去年同期" in question:
        # Same elapsed year-to-date period; Feb 29 is clamped to Feb 28.
        try:
            last_day = today.replace(year=today.year - 1)
        except ValueError:
            last_day = date(today.year - 1, 2, 28)
        ranges["去年同期"] = {
            "start": date(today.year - 1, 1, 1).isoformat(),
            "end_exclusive": (last_day + timedelta(days=1)).isoformat(),
        }
    return ranges


@dataclass
class QueryContext:
    user: CurrentUser
    datasource_id: int
    conversation_id: int
    question: str
    timezone: str = "Asia/Shanghai"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    manual_sql: str | None = field(default=None, repr=False)
    clarification_used: bool = False
    clarification_choice: str | None = None
    message_id: int | None = None
    query_run_id: int | None = None
    datasource: Datasource | None = field(default=None, repr=False)
    scope: VisibleScope | None = field(default=None, repr=False)
    history: list[MessageView] = field(default_factory=list, repr=False)
    retrieval: RetrievalBundle = field(default_factory=RetrievalBundle, repr=False)
    time_ranges: dict[str, Any] = field(default_factory=dict)
    follow_up: bool = False
    inherited_sql: str | None = None
    model_id: str = ""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    generated_sql: str | None = field(default=None, repr=False)
    guarded_sql: GuardedSql | None = field(default=None, repr=False)
    result: QueryResult | None = field(default=None, repr=False)
    retries: list[dict[str, Any]] = field(default_factory=list)
    chart: ChartSpec | None = None
    explanation: str = ""
    clarification: Clarification | None = None
    timings: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "success"
    error_code: ErrorCode | None = None
    execution_error: ErrorCode | None = None
    warnings: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    emit: Callable[[EventType, dict[str, Any]], Awaitable[None]] | None = field(
        default=None, repr=False
    )

    async def event(self, kind: EventType, data: dict[str, Any]) -> None:
        if self.emit:
            await self.emit(kind, data)

    def snapshot(self) -> dict[str, Any]:
        # No secrets, withheld metadata, full results or raw provider exception text.
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "question": self.question,
            "user_id": self.user.id,
            "team_id": self.user.team_id,
            "datasource_id": self.datasource_id,
            "model_id": self.model_id,
            "visible_schema": self.retrieval.tables,
            "retrieval": self.retrieval.model_dump(mode="json"),
            "history": [message.model_dump(mode="json") for message in self.history],
            "follow_up": self.follow_up,
            "inherited_sql": self.inherited_sql,
            "generated_sql": self.generated_sql,
            "time_ranges": self.time_ranges,
            "normalized_sql": self.guarded_sql.normalized_sql if self.guarded_sql else None,
            "guard": {
                "tables": sorted(self.guarded_sql.tables),
                "columns": {name: sorted(cols) for name, cols in self.guarded_sql.columns.items()},
            }
            if self.guarded_sql
            else None,
            "result_summary": {
                "row_count": self.result.row_count,
                "truncated": self.result.truncated,
            }
            if self.result
            else None,
            "retries": self.retries,
            "chart": self.chart.model_dump() if self.chart else None,
            "stage_timings": self.timings,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "prompt_versions": self.prompt_versions,
            "semantic": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind in {"semantic", "enum"}
            ],
            "metrics": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind == "metric"
            ],
            "examples": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind == "example"
            ],
            "time_range": self.time_ranges,
            "retry_count": len(self.retries),
            "status": self.status,
            "error": self.error_code,
            "warnings": self.warnings,
            "clarification": self.clarification.model_dump() if self.clarification else None,
        }

    def reply(self) -> QueryReply:
        return QueryReply(
            status=self.status,  # type: ignore[arg-type]
            query_run_id=self.query_run_id,
            message_id=self.message_id,
            code=self.error_code or ErrorCode.OK,
            sql=self.guarded_sql.normalized_sql
            if self.guarded_sql and self.status == "success"
            else None,
            columns=self.result.columns if self.result and self.status == "success" else [],
            rows=self.result.rows if self.result and self.status == "success" else [],
            truncated=self.result.truncated if self.result else False,
            chart=self.chart,
            explanation=self.explanation,
            clarification=self.clarification,
        )


class Stage(Protocol):
    name: str

    async def run(self, ctx: QueryContext) -> QueryContext: ...


class SqlOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str


class ClarifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clarify: bool


class PipelineDependencies:
    def __init__(
        self,
        permissions: PermissionService,
        datasource: Callable[[int, CurrentUser], Awaitable[Datasource]],
        history: Callable[[int], Awaitable[list[MessageView]]],
        builder: ContextBuilder,
        llm: LLMClient,
        guard: SqlGuard,
        executor: SqlExecutor,
        querylog: QueryLogWriter,
        prompts: PromptRegistry | None = None,
        limits: QueryLimits | None = None,
    ) -> None:
        self.permissions, self.datasource, self.history = permissions, datasource, history
        self.builder, self.llm, self.guard = builder, llm, guard
        self.executor, self.querylog = executor, querylog
        self.prompts = prompts or PromptRegistry()
        self.limits = limits or QueryLimits(user_id=1)

    def limits_for(self, user_id: int) -> QueryLimits:
        return self.limits.model_copy(update={"user_id": user_id})

    async def call(self, ctx: QueryContext, prompt_id: PromptId, data: dict[str, Any]) -> str:
        rendered = self.prompts.render(
            prompt_id,
            jsonable_encoder(data),
            dialect_hint=DialectAdapter(ctx.datasource.dialect).prompt_hint
            if ctx.datasource
            else "",
        )
        ctx.prompt_versions[prompt_id.value] = rendered.version
        response = await self.llm.chat(
            [ChatMessage(role="system", content=rendered.content)],
            CallContext(
                user_id=ctx.user.id,
                team_id=ctx.user.team_id,
                prompt_version=f"{prompt_id.value}:{rendered.version}",
            ),
        )
        ctx.model_id = response.model_id
        ctx.tokens_in += response.usage.tokens_in
        ctx.tokens_out += response.usage.tokens_out
        return response.text

    @staticmethod
    def data(ctx: QueryContext) -> dict[str, Any]:
        return {
            "question": ctx.question,
            "history": [{"role": item.role, "content": item.content} for item in ctx.history],
            "context": ctx.retrieval.model_dump(mode="json"),
            "time_ranges": ctx.time_ranges,
            "inherited_sql": ctx.inherited_sql,
            "clarification_choice": ctx.clarification_choice,
        }


class LoadContextStage:
    name = "load_context"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        ctx.history = (await self.deps.history(ctx.conversation_id))[-12:]
        # Deterministic bounded extractive summary; no separate unversioned model call.
        ctx.history = [
            item.model_copy(update={"content": item.content[:800]})
            for item in ctx.history
            if item.id != ctx.message_id
        ]
        ctx.follow_up = bool(ctx.history) and bool(
            re.search(
                r"^(那|再|其中|这些|它们|按|改成|只看|继续|what about|and )", ctx.question, re.I
            )
        )
        ctx.time_ranges = resolve_time_ranges(ctx.question, ctx.timezone, ctx.now)
        return ctx


class VisibleScopeStage:
    name = "visible_scope"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        ctx.scope = await self.deps.permissions.visible_scope(ctx.user, ctx.datasource_id)
        ctx.datasource = await self.deps.datasource(ctx.datasource_id, ctx.user)
        if ctx.datasource.id != ctx.datasource_id:
            raise AppError(ErrorCode.SQL_OUT_OF_SCOPE)
        # Assistant content may refer to objects revoked since the previous turn.
        allowed_history = []
        for item in ctx.history:
            if item.role == "user":
                allowed_history.append(item)
            elif item.normalized_sql:
                try:
                    sql = self.deps.guard.check(
                        item.normalized_sql, ctx.datasource.dialect, ctx.scope
                    )
                except AppError:
                    continue
                allowed_history.append(item)
                if ctx.follow_up:
                    ctx.inherited_sql = sql.normalized_sql
        ctx.history = allowed_history
        return ctx


class RetrievalStage:
    name = "retrieval"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.scope is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        ctx.retrieval = await self.deps.builder.build(
            ctx.user, ctx.scope, ctx.question, ctx.clarification_choice
        )
        return ctx


class ClarificationStage:
    name = "clarification"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.manual_sql or ctx.clarification_used:
            return ctx
        candidates = list(ctx.retrieval.ambiguities)
        if ctx.retrieval.required_conditions:
            choices = ctx.retrieval.required_conditions[:8]
            if len(choices) == 1:
                choices = [choices[0], "补充其他条件"]
            candidates.append(Clarification(question="请选择必要条件", choices=choices))
        if ("最近" in ctx.question or "近来" in ctx.question) and not any(
            "end_exclusive" in value
            for value in ctx.time_ranges.values()
            if isinstance(value, dict)
        ):
            candidates.append(Clarification(question="请选择时间范围", choices=["近7天", "近30天"]))
        decision = ClarifyOutput.model_validate_json(
            await self.deps.call(
                ctx,
                PromptId.CLARIFY,
                {**self.deps.data(ctx), "candidates": [item.model_dump() for item in candidates]},
            )
        )
        if candidates or decision.clarify:
            # Output options derive solely from structured retrieval/time rules.
            if not candidates:
                choices = [
                    f"{table}.{column}"
                    for table, columns in ctx.retrieval.tables.items()
                    for column in columns
                ][:8]
                if len(choices) < 2:
                    choices = ["按当前条件查询", "补充查询条件"]
                candidates = [Clarification(question="请选择要查询的字段", choices=choices)]
            ctx.clarification = candidates[0]
            ctx.status = "clarify"
            await ctx.event(
                EventType.EXPLANATION, {"clarification": ctx.clarification.model_dump()}
            )
        return ctx


class GenerateSqlStage:
    name = "generate_sql"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.manual_sql:
            ctx.generated_sql = ctx.manual_sql
        else:
            output = SqlOutput.model_validate_json(
                await self.deps.call(ctx, PromptId.SQL, self.deps.data(ctx))
            )
            ctx.generated_sql = output.sql
        return ctx


class GuardStage:
    name = "sql_guard"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.scope is None or ctx.datasource is None or ctx.generated_sql is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        ctx.scope = await self.deps.permissions.visible_scope(ctx.user, ctx.datasource_id)
        ctx.guarded_sql = self.deps.guard.check(
            ctx.generated_sql, ctx.datasource.dialect, ctx.scope
        )
        await ctx.event(EventType.SQL, {"sql": ctx.guarded_sql.normalized_sql})
        return ctx


class ExecuteStage:
    name = "execute"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.datasource is None or ctx.guarded_sql is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        ctx.execution_error = None
        try:
            ctx.result = await self.deps.executor.run(
                ctx.datasource, ctx.guarded_sql, self.deps.limits_for(ctx.user.id)
            )
        except AppError as error:
            if error.code != ErrorCode.SQL_EXECUTION_ERROR or ctx.manual_sql:
                raise AppError(error.code, http_status=error.http_status) from None
            ctx.execution_error = error.code
        return ctx


class CorrectionStage:
    name = "correction"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        while ctx.execution_error and len(ctx.retries) < 3:
            error = ctx.execution_error
            ctx.retries.append({"attempt": len(ctx.retries) + 1, "code": error.value})
            output = SqlOutput.model_validate_json(
                await self.deps.call(
                    ctx,
                    PromptId.CORRECT,
                    {
                        **self.deps.data(ctx),
                        "sql": ctx.generated_sql,
                        "error_category": error.value,
                    },
                )
            )
            ctx.generated_sql = output.sql
            # Every correction uses the same guard. Its rejection exits immediately.
            await GuardStage(self.deps).run(ctx)
            await ExecuteStage(self.deps).run(ctx)
        if ctx.execution_error:
            raise AppError(ctx.execution_error)
        return ctx


class PresentationStage:
    name = "presentation"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.result is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        data = {
            **self.deps.data(ctx),
            "columns": ctx.result.columns,
            "column_types": {
                column: sorted({type(row.get(column)).__name__ for row in ctx.result.rows[:5]})
                for column in ctx.result.columns
            },
            "row_count": ctx.result.row_count,
            "preview": ctx.result.rows[:5],
            "sql": ctx.guarded_sql.normalized_sql if ctx.guarded_sql else None,
        }
        try:
            chart = ChartSpec.model_validate_json(await self.deps.call(ctx, PromptId.CHART, data))
            if (
                (chart.x and chart.x not in ctx.result.columns)
                or any(column not in ctx.result.columns for column in chart.y)
                or (chart.type != "table" and (chart.x is None or not chart.y))
            ):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            ctx.chart = chart
        except (ValidationError, AppError) as error:
            if isinstance(error, AppError) and error.code not in {
                ErrorCode.LLM_UNAVAILABLE,
                ErrorCode.VALIDATION_ERROR,
            }:
                raise AppError(error.code, http_status=error.http_status) from None
            ctx.chart = ChartSpec(type="table")
            ctx.warnings.append("chart_fallback")
        try:
            output = Explanation.model_validate_json(
                await self.deps.call(ctx, PromptId.EXPLAIN, data)
            )
            ctx.explanation = output.summary
        except (ValidationError, AppError) as error:
            if isinstance(error, AppError) and error.code not in {
                ErrorCode.LLM_UNAVAILABLE,
                ErrorCode.VALIDATION_ERROR,
            }:
                raise AppError(error.code, http_status=error.http_status) from None
            ctx.explanation = "查询完成。"
            ctx.warnings.append("explanation_fallback")
        definitions = [
            f"{item.kind} {item.name}：{item.scope.value}级，版本{item.version}，"
            f"定义：{item.definition}"
            for item in ctx.retrieval.items
        ]
        ctx.explanation += "\n生效语义/指标：" + ("；".join(definitions) or "未命中")
        ctx.explanation += "\n过滤条件与时间范围以已校验 SQL 为准：" + (
            ctx.guarded_sql.normalized_sql if ctx.guarded_sql else ""
        )
        ctx.explanation += "\n后端计算的时间范围：" + json.dumps(
            ctx.time_ranges, ensure_ascii=False
        )
        if not ctx.result.rows:
            ctx.explanation += "\n结果为空，可能是条件过严或口径不符。"
        if ctx.result.truncated:
            ctx.explanation += "\n结果达到行数上限，已截断。"
        await ctx.event(
            EventType.DATA,
            {
                "columns": ctx.result.columns,
                "rows": ctx.result.rows,
                "truncated": ctx.result.truncated,
            },
        )
        await ctx.event(EventType.CHART, ctx.chart.model_dump())
        await ctx.event(EventType.EXPLANATION, {"text": ctx.explanation})
        return ctx


class RecordStage:
    name = "record"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.status == "blocked":
            # A rejected correction may still hold an earlier execution capability.
            # Persist only the category, never either rejected or previously guarded SQL.
            ctx.generated_sql = None
            ctx.guarded_sql = None
            ctx.result = None
            ctx.retrieval = RetrievalBundle()
            ctx.history = []
            ctx.inherited_sql = None
        ctx.query_run_id = await self.deps.querylog.write(
            ctx.user,
            ctx.datasource_id,
            ctx.status,
            sql=ctx.guarded_sql,
            result=ctx.result,
            blocked_code=ctx.error_code if ctx.status == "blocked" else None,
            prompt_versions=ctx.prompt_versions,
            tokens_in=ctx.tokens_in,
            tokens_out=ctx.tokens_out,
            details=ctx.snapshot(),
            duration_ms=int((time.monotonic() - ctx.started) * 1000),
        )
        return ctx


class QueryPipeline:
    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps
        self.stages: list[Stage] = [
            LoadContextStage(deps),
            VisibleScopeStage(deps),
            RetrievalStage(deps),
            ClarificationStage(deps),
            GenerateSqlStage(deps),
            GuardStage(deps),
            ExecuteStage(deps),
            CorrectionStage(deps),
            PresentationStage(deps),
            RecordStage(deps),
        ]

    async def run(self, ctx: QueryContext) -> QueryContext:
        cancelled = False
        try:
            for stage in self.stages[:-1]:
                started = time.monotonic()
                await ctx.event(EventType.STAGE, {"name": stage.name})
                try:
                    ctx = await stage.run(ctx)
                finally:
                    ctx.timings[stage.name] = int((time.monotonic() - started) * 1000)
                if ctx.status == "clarify":
                    break
        except asyncio.CancelledError:
            cancelled = True
            ctx.status, ctx.error_code = "failed", ErrorCode.QUERY_CANCELLED
        except AppError as error:
            ctx.error_code = error.code
            ctx.status = (
                "blocked"
                if isinstance(error, SqlBlocked)
                or error.code.value.startswith("SQL_")
                and error.code != ErrorCode.SQL_EXECUTION_ERROR
                else "failed"
            )
            ctx.explanation = MESSAGES[error.code]
        except ValidationError:
            ctx.status, ctx.error_code = "failed", ErrorCode.VALIDATION_ERROR
            ctx.explanation = MESSAGES[ErrorCode.VALIDATION_ERROR]
        except Exception:
            ctx.status, ctx.error_code = "failed", ErrorCode.INTERNAL_ERROR
            ctx.explanation = MESSAGES[ErrorCode.INTERNAL_ERROR]
        finally:
            # RecordStage always runs, including failures, clarification and disconnect.
            ctx.timings["record"] = 0
            if cancelled:
                await asyncio.shield(self.stages[-1].run(ctx))
            else:
                await ctx.event(EventType.STAGE, {"name": self.stages[-1].name})
                await self.stages[-1].run(ctx)
        if ctx.error_code:
            await ctx.event(
                EventType.ERROR, {"code": ctx.error_code.value, "message": MESSAGES[ctx.error_code]}
            )
        await ctx.event(EventType.DONE, {"status": ctx.status, "query_run_id": ctx.query_run_id})
        if cancelled:
            raise AppError(ErrorCode.QUERY_CANCELLED, http_status=499)
        return ctx


def encode_event(identifier: int, kind: EventType, data: dict[str, Any]) -> StreamEvent:
    return StreamEvent(id=identifier, event=kind, data=data)
