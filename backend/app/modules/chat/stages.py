"""Stages from loading history to the corrected, executed SQL, plus the query-log record."""

import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.audit import QueryLogWriter
from app.core.errors import AppError, BlockRule, ErrorCode, SqlBlocked, UnresolvedReference
from app.core.llm import extract_json_object
from app.core.prompt import PromptId
from app.core.sql.guard import referenced_tables
from app.core.stream import EventType
from app.modules.chat.dependencies import PipelineDependencies
from app.modules.chat.query_context import QueryContext
from app.modules.chat.schemas import Clarification, RetrievalBundle
from app.modules.chat.time_ranges import resolve_time_ranges

# A follow-up can build on at most this many earlier question/answer rounds.
HISTORY_ROUNDS = 5
# snake_case or dotted ASCII names: table and column identifiers, never business wording.
_IDENTIFIER = re.compile(r"[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Za-z_]+\.[A-Za-z_]+")


class ClarifyChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(min_length=1, max_length=200)
    ref: str = Field(min_length=1, max_length=300)


class ClarifyOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    clarify: bool = True
    question: str = Field(default="", max_length=500)
    choices: list[ClarifyChoice] = Field(default_factory=list, max_length=8)


class SqlOutput(BaseModel):
    """One model call: the SQL plus how to present it (or a clarification instead)."""

    model_config = ConfigDict(extra="ignore")
    sql: str = ""
    # The model reports that the visible schema cannot answer the question.
    unanswerable: bool = False
    # The model reports a request to change data or bypass the rules.
    write_request: bool = False
    clarify: ClarifyOutput | None = None
    title: str = Field(default="", max_length=200)
    interpretation: str = Field(default="", max_length=1000)
    filters: list[str] = Field(default_factory=list, max_length=50)
    assumption: str = Field(default="", max_length=500)
    # Validated later against the real result columns; never trusted as is.
    chart: dict[str, Any] | None = None

    # The display fields are best-effort text for the user. A model that writes too much
    # or uses the wrong JSON type must not throw away a valid SQL answer.
    @field_validator("sql", mode="before")
    @classmethod
    def _sql_text(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("title", "interpretation", "assumption", mode="before")
    @classmethod
    def _short_text(cls, value: Any, info: Any) -> str:
        limit = {"title": 200, "interpretation": 1000, "assumption": 500}[info.field_name]
        if value is None:
            return ""
        return (value if isinstance(value, str) else str(value))[:limit]

    @field_validator("filters", mode="before")
    @classmethod
    def _filter_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            return []
        return [str(item)[:200] for item in items if item is not None][:50]

    @field_validator("chart", mode="before")
    @classmethod
    def _chart_object(cls, value: Any) -> Any:
        return value if isinstance(value, dict) else None


def verified_choices(output: ClarifyOutput, retrieval: RetrievalBundle) -> list[tuple[str, str]]:
    """Keep only choices whose ref names a visible column or retrieved metric.

    A model-proposed option that cannot be verified against the authorized context is
    dropped, so clarification never reveals or invents objects outside the visible scope.
    Returns (label, ref) pairs so the chosen option can later pin its column or metric.
    """
    columns = {
        f"{table}.{column}".casefold()
        for table, table_columns in retrieval.tables.items()
        for column in table_columns
    }
    short = {
        f"{table.rsplit('.', 1)[-1]}.{column}".casefold()
        for table, table_columns in retrieval.tables.items()
        for column in table_columns
    }
    metrics = {
        f"metric:{item.name}".casefold() for item in retrieval.items if item.kind == "metric"
    }
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    labels: set[str] = set()
    for choice in output.choices:
        ref = choice.ref.strip().casefold()
        label = choice.label.strip()
        if (
            (ref in columns or ref in short or ref in metrics)
            and ref not in seen
            and label
            and label not in labels
        ):
            seen.add(ref)
            labels.add(label)
            pairs.append((label, choice.ref.strip()))
    return pairs


class LoadContextStage:
    name = "load_context"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        recent = await self.deps.history(ctx.conversation_id)
        # Deterministic bounded extraction: the current question is excluded, answers keep only
        # their first line (the SQL travels separately), and rounds beyond the cap are dropped.
        ctx.history = [
            item.model_copy(
                update={
                    "content": item.content[:800]
                    if item.role == "user"
                    else item.content.split("\n", 1)[0][:300]
                }
            )
            for item in recent
            if item.id != ctx.message_id
        ][-2 * HISTORY_ROUNDS :]
        # Whether the question builds on earlier rounds is the model's call (prompt rule); the
        # latest guard-approved SQL is always offered as the candidate to inherit.
        ctx.follow_up = bool(ctx.history)
        # A picked time option ("近7天") resolves together with the original question.
        ctx.time_ranges = resolve_time_ranges(
            f"{ctx.question}\n{ctx.clarification_choice or ''}", ctx.timezone, ctx.now
        )
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
                if sql.output_masks:
                    # The answer was written before today's masks: its text may quote values
                    # that are masked now. Keep the SQL for follow-ups, never the old text.
                    item = item.model_copy(update={"content": ""})
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
        ctx.retrieval = await build_context(self.deps, ctx)
        return ctx


async def build_context(
    deps: "PipelineDependencies", ctx: QueryContext, full: bool = False
) -> RetrievalBundle:
    """Retrieve this question's context; full=True is the fallback when retrieval missed."""
    if ctx.scope is None:
        raise AppError(ErrorCode.INTERNAL_ERROR)
    earlier = [message.content for message in ctx.history if message.role == "user"][-1:]
    return await deps.builder.build(
        ctx.user,
        ctx.scope,
        ctx.question,
        ctx.clarification_choice,
        ctx.pinned_metric_ids,
        ctx.clarification_ref,
        full=full,
        follow_up=" ".join(earlier) if ctx.inherited_sql else "",
        keep_tables=referenced_tables(ctx.inherited_sql) if ctx.inherited_sql else frozenset(),
    )


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
        if not candidates:
            # Model-judged ambiguity is decided inside the single SQL call (GenerateSqlStage).
            return ctx
        # Deterministic backend signals always clarify; no model call is needed.
        await ask_clarification(ctx, candidates[0], [])
        return ctx


async def ask_clarification(
    ctx: QueryContext, clarification: Clarification, refs: list[str]
) -> None:
    ctx.clarification, ctx.clarification_refs = clarification, refs
    ctx.status = "clarify"
    await ctx.event(EventType.EXPLANATION, {"clarification": clarification.model_dump()})


class GenerateSqlStage:
    """One streamed model call: SQL, or a verified clarification, plus presentation hints."""

    name = "generate_sql"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.manual_sql:
            ctx.generated_sql = ctx.manual_sql
            return ctx
        data = self.deps.data(ctx)
        output = SqlOutput.model_validate_json(
            extract_json_object(await self.deps.stream_call(ctx, PromptId.SQL, data))
        )
        if output.write_request:
            # Refuse the whole request rather than quietly answering only its read part.
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT, BlockRule.WRITE_REQUEST)
        if output.clarify is not None and output.clarify.clarify and data["clarification_allowed"]:
            pairs = verified_choices(output.clarify, ctx.retrieval)
            if len(pairs) >= 2:
                await ask_clarification(
                    ctx,
                    Clarification(
                        question=output.clarify.question.strip() or "请选择要使用的口径",
                        choices=[label for label, _ in pairs[:8]],
                    ),
                    [ref for _, ref in pairs[:8]],
                )
                return ctx
        if output.clarify is not None and not output.sql.strip() and not output.unanswerable:
            # Unverifiable or disallowed options never block the question: ask once for SQL.
            output = SqlOutput.model_validate_json(
                extract_json_object(
                    await self.deps.stream_call(
                        ctx, PromptId.SQL, {**data, "clarification_allowed": False}
                    )
                )
            )
        if output.unanswerable and ctx.retrieval.mode == "hybrid":
            # Retrieval may have left out what the question needs: ask once more with the
            # whole visible schema before refusing.
            ctx.retrieval = await build_context(self.deps, ctx, full=True)
            ctx.retrieval_fallback = "unanswerable"
            output = SqlOutput.model_validate_json(
                extract_json_object(
                    await self.deps.stream_call(
                        ctx, PromptId.SQL, {**self.deps.data(ctx), "clarification_allowed": False}
                    )
                )
            )
            if output.write_request:
                raise SqlBlocked(ErrorCode.SQL_NOT_SELECT, BlockRule.WRITE_REQUEST)
        if output.unanswerable or not output.sql.strip():
            # Nothing was generated or executed; report only the category, never object names.
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.UNANSWERABLE)
        ctx.generated_sql = output.sql
        ctx.title = " ".join(output.title.split())[:20]
        ctx.interpretation = output.interpretation.strip()[:500]
        ctx.filters = [item.strip()[:200] for item in output.filters if item.strip()][:20]
        assumption = " ".join(output.assumption.split())[:100]
        # Shown to the user verbatim: drop it rather than show a column name or code.
        ctx.assumption = "" if _IDENTIFIER.search(assumption) else assumption
        ctx.chart_hint = output.chart
        return ctx


class GuardStage:
    name = "sql_guard"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.scope is None or ctx.datasource is None or ctx.generated_sql is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        ctx.scope = await self.deps.permissions.visible_scope(ctx.user, ctx.datasource_id)
        ctx.guarded_sql = None
        try:
            ctx.guarded_sql = self.deps.guard.check(
                ctx.generated_sql, ctx.datasource.dialect, ctx.scope
            )
        except UnresolvedReference as error:
            ctx.rejected_sql = ctx.generated_sql
            if ctx.manual_sql or len(ctx.retries) >= self.deps.max_corrections:
                raise
            # A field the model made up: let the correction round fix it. Nothing ran.
            ctx.execution_error, ctx.error_detail = error.code, error.detail
            ctx.execution_rule = error.rule.value if error.rule else None
            return ctx
        except SqlBlocked as error:
            ctx.rejected_sql = ctx.generated_sql
            if (
                error.rule != BlockRule.FUNCTION_NOT_REVIEWED
                or ctx.manual_sql
                or ctx.function_rewritten
                or len(ctx.retries) >= self.deps.max_corrections
            ):
                # Scope, writes, several statements, dangerous or unknown functions: final.
                raise
            # A standard function not reviewed for this database, nothing else wrong so far
            # and nothing ran: ask once for another way; the rewrite passes the whole guard.
            ctx.function_rewritten = True
            ctx.execution_error, ctx.execution_rule = error.code, error.rule.value
            ctx.execution_subject = error.subject
            ctx.error_detail = function_hint(error.subject, ctx.datasource.dialect)
            return ctx
        await ctx.event(EventType.SQL, {"sql": ctx.guarded_sql.normalized_sql})
        return ctx


def function_hint(function: str | None, dialect: str) -> str:
    named = f"函数 {function.upper()}" if function else "SQL 中的某个函数"
    return (
        f"{named} 未经 {dialect} 审核，不能使用。请只用常见标准函数改写"
        "（如 SUBSTRING、CONCAT、CAST、COALESCE、TRIM、UPPER、LOWER、EXTRACT、DATE_TRUNC、ROUND），"
        "不需要时直接去掉；其余部分保持不变。"
    )


class ExecuteStage:
    name = "execute"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.guarded_sql is None and ctx.execution_error is not None:
            return ctx  # The guard asked for a correction; nothing to execute yet.
        if ctx.datasource is None or ctx.guarded_sql is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        ctx.execution_error, ctx.error_detail, ctx.execution_rule = None, "", None
        ctx.execution_subject = None
        try:
            limits = self.deps.limits_for(ctx.user.id)
            if ctx.limit_overrides:
                limits = limits.model_copy(update=ctx.limit_overrides)
            ctx.result = await self.deps.executor.run(ctx.datasource, ctx.guarded_sql, limits)
        except AppError as error:
            if isinstance(error, SqlBlocked):
                raise  # Keeps the rule (cost, mismatch) for diagnostics.
            if error.code != ErrorCode.SQL_EXECUTION_ERROR or ctx.manual_sql:
                if getattr(error, "subject", None):
                    raise  # Core errors carry only a standard code; keep it for diagnostics.
                raise AppError(error.code, http_status=error.http_status) from None
            ctx.execution_error = error.code
            rule = getattr(error, "rule", None)
            ctx.execution_rule = rule.value if rule else None
            ctx.execution_subject = getattr(error, "subject", None)
            ctx.error_detail = getattr(error, "detail", "") or ""
        return ctx


class CorrectionStage:
    name = "correction"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        while ctx.execution_error and len(ctx.retries) < self.deps.max_corrections:
            error = ctx.execution_error
            ctx.retries.append(
                {"attempt": len(ctx.retries) + 1, "code": error.value}
                | ({"rule": ctx.execution_rule} if ctx.execution_rule else {})
                | ({"subject": ctx.execution_subject} if ctx.execution_subject else {})
            )
            if ctx.retrieval.mode == "hybrid":
                # A correction may need a column retrieval left out: give it the whole schema.
                ctx.retrieval = await build_context(self.deps, ctx, full=True)
                ctx.retrieval_fallback = "correction"
            request = {
                **self.deps.data(ctx),
                "sql": ctx.generated_sql,
                "error_category": error.value,
            }
            if ctx.error_detail:
                request["error_detail"] = ctx.error_detail
            output = SqlOutput.model_validate_json(
                extract_json_object(await self.deps.call(ctx, PromptId.CORRECT, request))
            )
            if output.unanswerable or not output.sql.strip():
                raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.UNANSWERABLE)
            ctx.generated_sql = output.sql
            # Every correction passes the same guard; only unresolved fields get another round.
            await GuardStage(self.deps).run(ctx)
            await ExecuteStage(self.deps).run(ctx)
        if ctx.execution_error:
            if ctx.execution_error == ErrorCode.SQL_OUT_OF_SCOPE:
                raise SqlBlocked(ctx.execution_error, BlockRule.CORRECTION_OUT_OF_SCOPE)
            raise AppError(ctx.execution_error)
        return ctx


class RecordStage:
    name = "record"

    def __init__(self, deps: PipelineDependencies, writer: QueryLogWriter | None = None) -> None:
        self.deps = deps
        self.writer = writer

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
        ctx.query_run_id = await (self.writer or self.deps.querylog).write(
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
