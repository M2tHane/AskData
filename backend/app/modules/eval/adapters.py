"""I3 factories for isolated fixture replay through the production chat pipeline."""

import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from copy import copy, deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.core.audit import QueryLogWriter
from app.core.config import Settings
from app.core.datasource import Datasource
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.llm import (
    CallContext,
    CallRecord,
    ChatMessage,
    ChatResponse,
    LLMClient,
    ModelConfig,
    ModelRegistry,
    StreamChunk,
    Usage,
    UsageRecorder,
)
from app.core.permission import PermissionService
from app.core.sql.executor import QueryLimits, QueryResult, SqlExecutor
from app.core.sql.guard import GuardedSql, SqlGuard
from app.modules.chat.service import (  # type: ignore[attr-defined]
    ContextBuilder,
    MessageView,
    PipelineDependencies,
    QueryContext,
    QueryPipeline,
    ScopeOnlyIndex,
)
from app.modules.eval.assets import EvalAssets
from app.modules.eval.schemas import EvalCaseInput, Evaluation, PipelineOutcome, RunRequest
from app.modules.eval.scoring import evaluate, summarize
from app.modules.eval.service import EvalQuestion, GuardedReferenceRunner, case_clock


class FixtureBindings(BaseModel):
    """Explicit aliases to identities/connections already provisioned by public services."""

    team_ids: dict[str, int]
    users: dict[str, CurrentUser]
    datasources: dict[str, Datasource] = Field(repr=False)
    # Keys emitted by the real retrieval service -> versioned fixture IDs.
    semantic_aliases: dict[str, str] = Field(default_factory=dict)
    metric_aliases: dict[str, str] = Field(default_factory=dict)


# Lexical mapping only, never SQL validation. SQL always proceeds to the core guard.
# Strings, dollar strings and comments are opaque; nested comments/escape strings
# are rejected when a schema rewrite is needed rather than risking a wrong rewrite.
_SQL_TOKENS = re.compile(
    r"(?P<string>'(?:''|[^'])*')|(?P<dollar>\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*)?\$"
    r".*?\$(?P=tag)\$)|(?P<empty_dollar>\$\$.*?\$\$)|"
    r'(?P<quoted>"(?:""|[^"])*")|(?P<comment>--[^\n]*|/\*.*?\*/)|'
    r"(?P<identifier>[A-Za-z_][A-Za-z_0-9$]*)|(?P<space>\s+)|(?P<other>.)",
    re.DOTALL,
)


def rewrite_schema(sql: str, source: str, target: str) -> str:
    """Map qualified fixture identifiers without changing literal data or comments."""
    if source == target:
        return sql
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", target):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    tokens = list(_SQL_TOKENS.finditer(sql))
    if any(
        (token.lastgroup == "comment" and token.group().count("/*") > 1)
        or (token.lastgroup == "string" and "\\" in token.group())
        for token in tokens
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    output = []
    for index, token in enumerate(tokens):
        value = token.group()
        name = value[1:-1].replace('""', '"') if token.lastgroup == "quoted" else value.lower()
        if token.lastgroup in {"identifier", "quoted"} and name == source:
            next_index = index + 1
            while next_index < len(tokens) and tokens[next_index].lastgroup in {"space", "comment"}:
                next_index += 1
            if next_index < len(tokens) and tokens[next_index].group() == ".":
                value = f'"{target}"'
        output.append(value)
    return "".join(output)


class MappedEvalAssets(EvalAssets):
    def __init__(
        self, directory: Path, bindings: FixtureBindings, permissions: PermissionService | None
    ) -> None:
        super().__init__(directory)
        if (
            set(bindings.team_ids) != set(self.team_ids)
            or set(bindings.users) != set(self.users)
            or set(bindings.datasources) != set(self.datasource_ids)
            or any(identifier < 1 for identifier in bindings.team_ids.values())
            or len(set(bindings.team_ids.values())) != len(bindings.team_ids)
            or len({user.id for user in bindings.users.values()}) != len(bindings.users)
            or len({ds.id for ds in bindings.datasources.values()}) != len(bindings.datasources)
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        for alias, baseline in self.users.items():
            mapped = bindings.users[alias]
            team_alias = next(
                (name for name, value in self.team_ids.items() if value == baseline.team_id), None
            )
            if (
                not mapped.active
                or mapped.id < 1
                or mapped.role != baseline.role
                or mapped.team_id != bindings.team_ids.get(team_alias or "")
            ):
                raise AppError(ErrorCode.VALIDATION_ERROR)
        self.bindings = bindings.model_copy(deep=True)
        self.original_schemas = {
            alias: item["schema"] for alias, item in self.fixtures["datasources"].items()
        }
        self.fixtures = deepcopy(self.fixtures)
        for alias, datasource in self.bindings.datasources.items():
            if datasource.id < 1 or not datasource.read_only or datasource.dialect != "postgres":
                raise AppError(ErrorCode.VALIDATION_ERROR)
            rewrite_schema("", "fixture", datasource.schema_name)
            self.fixtures["datasources"][alias]["schema"] = datasource.schema_name
        self.team_ids = dict(self.bindings.team_ids)
        self.users = dict(self.bindings.users)
        self.datasource_ids = {name: ds.id for name, ds in self.bindings.datasources.items()}
        # Offline replay uses the fixture PermissionProvider through core. Production
        # evaluation must inject the provisioned grant service's PermissionService.
        if permissions is not None:
            self.permissions = permissions

    def map_sql(self, datasource: str, sql: str) -> str:
        if datasource not in self.bindings.datasources:
            raise AppError(ErrorCode.NOT_FOUND)
        return rewrite_schema(
            sql,
            self.original_schemas[datasource],
            self.bindings.datasources[datasource].schema_name,
        )

    async def resolve(self, name: str) -> Datasource:
        if name not in self.bindings.datasources:
            raise AppError(ErrorCode.NOT_FOUND)
        return self.bindings.datasources[name]

    async def hidden_names(self, case: EvalCaseInput) -> set[str]:
        # Compare the actual service's scope to the fixture catalog; never infer visibility.
        scope = await self.permissions.visible_scope(
            self.users[case.as_user], self.datasource_ids[case.datasource]
        )
        schema = self.fixtures["datasources"][case.datasource]["schema"]
        hidden: set[str] = set()
        for name, columns in self.fixtures["catalog"][case.datasource].items():
            table = scope.tables.get(f"{schema}.{name}")
            hidden.update(set(columns) - set(table.columns) if table else {name})
        return hidden


def build_fixture_assets(
    directory: Path,
    bindings: FixtureBindings,
    permissions: PermissionService | None = None,
) -> MappedEvalAssets:
    return MappedEvalAssets(directory, bindings, permissions)


class CountingExecutor(SqlExecutor):
    """Per-question conservative count of real core executor attempts, including failures.

    No global counters and no dependency mutation: concurrent cases/reference queries
    cannot contaminate the count. A safety result passes only when this count is zero.
    """

    def __init__(self, executor: SqlExecutor) -> None:
        self.executor, self.attempts = executor, 0

    async def run(
        self, datasource: Datasource, sql: GuardedSql, limits: QueryLimits
    ) -> QueryResult:
        self.attempts += 1
        return await self.executor.run(datasource, sql, limits)


DependencyFactory = Callable[[EvalQuestion, RunRequest], Awaitable[PipelineDependencies]]


class RealChatAdapter:
    def __init__(self, assets: MappedEvalAssets, dependencies: DependencyFactory) -> None:
        self.assets, self.dependencies = assets, dependencies

    async def run(self, question: EvalQuestion, config: RunRequest) -> PipelineOutcome:
        if (
            question.as_user not in self.assets.users
            or question.datasource not in self.assets.datasource_ids
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        clock = question.clock or self.assets.fixtures["clock"]
        try:
            now = datetime.fromisoformat(clock["now"])
            timezone = str(clock["timezone"])
            ZoneInfo(timezone)
            if now.tzinfo is None:
                raise AppError(ErrorCode.VALIDATION_ERROR)
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
            raise AppError(ErrorCode.VALIDATION_ERROR) from None
        deps = copy(
            await self.dependencies(question.model_copy(deep=True), config.model_copy(deep=True))
        )
        executor = CountingExecutor(deps.executor)
        deps.executor = executor
        deps.permissions = self.assets.permissions
        requested_versions = config.prompt_versions
        supported_versions = {key.value: value for key, value in deps.prompts.versions.items()}
        if any(supported_versions.get(key) != value for key, value in requested_versions.items()):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Unsupported evaluation prompt version")
        history = []
        try:
            for index, item in enumerate(question.history):
                raw_sql = item.get("sql") or item.get("normalized_sql")
                history.append(
                    MessageView(
                        id=index + 1,
                        conversation_id=0,
                        role=item["role"],
                        content=item["content"],
                        normalized_sql=self.assets.map_sql(question.datasource, raw_sql)
                        if raw_sql
                        else None,
                        created_at=now,
                    )
                )
        except (KeyError, TypeError, ValidationError):
            raise AppError(ErrorCode.VALIDATION_ERROR) from None

        async def load_history(identifier: int) -> list[MessageView]:
            return [item.model_copy(deep=True) for item in history]

        deps.history = load_history
        ctx = QueryContext(
            user=self.assets.users[question.as_user],
            datasource_id=self.assets.datasource_ids[question.datasource],
            conversation_id=0,
            question=question.question,
            now=now,
            timezone=timezone,
        )
        selected_model = await deps.llm.registry.select(
            self.assets.users[question.as_user].team_id, "llm"
        )
        if selected_model.id != config.model_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Evaluation model does not match run config")
        start = time.monotonic()
        await QueryPipeline(deps).run(ctx)
        if ctx.model_id and ctx.model_id != config.model_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Evaluation model does not match run config")
        if ctx.status not in {"success", "blocked", "clarify", "failed"}:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        chart = ctx.chart
        chart_valid = False
        if chart is not None:
            try:
                # Use the registered chat schema without importing another module's schemas.
                validated = type(chart).model_validate(chart.model_dump())
                columns = ctx.result.columns if ctx.result else []
                chart_valid = (
                    "chart_fallback" not in ctx.warnings
                    and (validated.x is None or validated.x in columns)
                    and all(column in columns for column in validated.y)
                    and (validated.type == "table" or bool(validated.x and validated.y))
                )
            except ValidationError:
                pass
        # No access to the data source at all is a refusal like any guard block.
        status = "blocked" if ctx.error_code == ErrorCode.FORBIDDEN else ctx.status
        return PipelineOutcome(
            status=status,  # type: ignore[arg-type]
            # Rejected SQL is kept for admins reviewing the run; leaders never see it.
            sql=ctx.guarded_sql.normalized_sql
            if ctx.guarded_sql and ctx.status == "success"
            else ctx.rejected_sql
            if status == "blocked"
            else None,
            result=ctx.result if ctx.status == "success" else None,
            blocked_code=ctx.error_code if status == "blocked" else None,
            executed_queries=executor.attempts,
            retries=len(ctx.retries),
            duration_ms=int((time.monotonic() - start) * 1000),
            tokens_in=ctx.tokens_in,
            tokens_out=ctx.tokens_out,
            message=ctx.explanation,
            clarification_options=ctx.clarification.choices if ctx.clarification else [],
            clarification_refs=ctx.clarification_refs if ctx.clarification else [],
            assumption=ctx.assumption,
            chart_type=chart.type if chart else None,
            chart_valid=chart_valid,
            semantic_ids=self._aliases(ctx, "semantic"),
            metric_ids=self._aliases(ctx, "metric"),
            time_range=self._time_range(ctx),
        )

    def _aliases(self, ctx: QueryContext, kind: Literal["semantic", "metric"]) -> list[str]:
        aliases = (
            self.assets.bindings.semantic_aliases
            if kind == "semantic"
            else self.assets.bindings.metric_aliases
        )
        return [
            # Same-caliber name resolves by scope precedence per user, so the merged
            # item's key alone cannot distinguish fixtures; the real ref ID can.
            aliases.get(f"{kind}:{item.ref_id}", aliases.get(item.key, item.key))
            for item in ctx.retrieval.items
            if item.kind == kind
        ]

    @staticmethod
    def _time_range(ctx: QueryContext) -> dict[str, str] | None:
        # Report only an unambiguous half-open interval observed in the executed,
        # guard-normalized single-table SQL. Complex expressions remain unreported.
        if not ctx.guarded_sql or ctx.status != "success" or len(ctx.guarded_sql.tables) != 1:
            return None
        sql = "".join(
            " " if token.lastgroup == "comment" else token.group()
            for token in _SQL_TOKENS.finditer(ctx.guarded_sql.normalized_sql)
        )
        if sql.upper().count("SELECT ") != 1 or re.search(
            r"\b(?:OR|CASE|WITH|UNION|INTERSECT|EXCEPT|EXISTS)\b", sql, re.IGNORECASE
        ):
            return None
        where = re.search(r"\bWHERE\b(.*)", sql, re.IGNORECASE | re.DOTALL)
        if where is None:
            return None
        conditions = re.split(r"\b(?:GROUP|ORDER|LIMIT|HAVING)\b", where[1], maxsplit=1)[0]
        pattern = (
            r'"([A-Za-z_][A-Za-z_0-9]*)"\s*(>=|<)\s*'
            r"(?:CAST\s*\(\s*)?'(\d{4}-\d{2}-\d{2})'(?:\s+AS\s+DATE\s*\))?"
        )
        intervals: dict[str, dict[str, str]] = {}
        for column, operator, value in re.findall(pattern, conditions):
            bound = "start" if operator == ">=" else "end"
            bounds = intervals.setdefault(column, {})
            if bound in bounds:
                return None
            bounds[bound] = value
        candidates = [(column, bounds) for column, bounds in intervals.items() if len(bounds) == 2]
        if len(candidates) != 1:
            return None
        column, bounds = candidates[0]
        table = next(iter(ctx.guarded_sql.tables))
        if column not in ctx.guarded_sql.columns.get(table, frozenset()):
            return None
        return {"field": f"{table.rsplit('.', 1)[-1]}.{column}", **bounds, "timezone": ctx.timezone}


def build_eval_adapter(
    assets: MappedEvalAssets, dependencies: DependencyFactory
) -> RealChatAdapter:
    """Factory gets input-only questions/config and must return request-local dependencies."""
    return RealChatAdapter(assets, dependencies)


def build_reference_runner(
    assets: MappedEvalAssets, guard: SqlGuard, executor: SqlExecutor
) -> GuardedReferenceRunner:
    return GuardedReferenceRunner(
        assets, assets, assets.permissions, guard, executor, sql_mapper=assets.map_sql
    )


async def run_suite(
    assets: MappedEvalAssets,
    pipeline: RealChatAdapter,
    references: GuardedReferenceRunner,
    config: RunRequest,
) -> tuple[list[Evaluation], dict[str, Any]]:
    """Standalone replay shares the same scoring and safety gate as background eval."""
    selected = [case for case in assets.cases if not config.case_ids or case.id in config.case_ids]
    if not selected or set(config.case_ids) - {case.id for case in selected}:
        raise AppError(ErrorCode.VALIDATION_ERROR)
    results = []
    for case in selected:
        try:
            reference = await references.result(case) if case.expect_status == "success" else None
            outcome = await pipeline.run(
                EvalQuestion(
                    case_id=case.id,
                    datasource=case.datasource,
                    as_user=case.as_user,
                    question=case.question,
                    history=case.history,
                    clock=case_clock(case, assets.fixtures["clock"]),
                    injection_context=case.injection_context,
                ),
                config,
            )
            results.append(evaluate(case, outcome, reference, await assets.hidden_names(case)))
        except AppError:
            results.append(
                Evaluation(
                    case_id=case.id, category=case.category, passed=False, reason="execution_failed"
                )
            )
    return results, summarize(results)


class EvaluationSettings(Settings):
    """Read-only fixture connections from ASKDATA_EVALUATION_DATASOURCES, via core settings."""

    evaluation_datasources: dict[str, Datasource] = Field(default_factory=dict, repr=False)


class _SimulationModels:
    def __init__(self, identifier: str) -> None:
        self.model = ModelConfig(
            id=identifier, kind="llm", model="fixed-eval", api_key=SecretStr(""), retries=0
        )

    async def configurations(self) -> list[ModelConfig]:
        return [self.model]

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        return self.model.id if kind == "llm" else None

    async def default(self, kind: str) -> str | None:
        return self.model.id if kind == "llm" else None


class SimulationUsage:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def authorize(self, context: CallContext) -> None:
        return None

    async def record(self, record: CallRecord) -> None:
        self.records.append(record)


class ScriptedTransport:
    """Explicit fixed responses; a missing response fails instead of consulting reference SQL."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.position = 0

    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse:
        if self.position >= len(self.responses):
            raise AppError(ErrorCode.LLM_UNAVAILABLE)
        response = self.responses[self.position]
        self.position += 1
        return ChatResponse(text=response, usage=Usage())

    async def stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(model, messages)
        yield StreamChunk(text=response.text, usage=response.usage)

    async def embed(self, model: ModelConfig, texts: list[str]) -> tuple[list[list[float]], Usage]:
        raise AppError(ErrorCode.LLM_UNAVAILABLE)

    async def rerank(self, model: ModelConfig, query: str, documents: list[str]) -> list[int]:
        raise AppError(ErrorCode.LLM_UNAVAILABLE)


def build_fixed_client(
    responses: list[str], model_id: str = "fixed", recorder: UsageRecorder | None = None
) -> LLMClient:
    return LLMClient(
        ModelRegistry(_SimulationModels(model_id)),
        recorder if recorder is not None else SimulationUsage(),
        ScriptedTransport(responses),
    )


def build_simulated_adapter(
    assets: MappedEvalAssets,
    responses: Mapping[str, list[str]],
    executor: SqlExecutor,
    querylog: QueryLogWriter,
    builder: ContextBuilder | None = None,
) -> RealChatAdapter:
    """Offline replay factory. Responses are independent artifacts keyed by case ID.

    The caller must map SQL in scripted responses to physical schemas. The factory
    never receives a case definition or derives an answer from expected_sql.
    Production I3 uses build_eval_adapter with actual service-backed dependencies.
    """
    scripts = deepcopy(dict(responses))

    async def dependencies(question: EvalQuestion, config: RunRequest) -> PipelineDependencies:
        if question.case_id not in scripts:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Missing simulated model responses")

        async def resolve(identifier: int, user: CurrentUser) -> Datasource:
            datasource = await assets.resolve(question.datasource)
            if datasource.id != identifier:
                raise AppError(ErrorCode.SQL_OUT_OF_SCOPE)
            return datasource

        async def history(identifier: int) -> list[MessageView]:
            return []

        threshold = config.config.get("retrieval_threshold", 30)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if builder is not None and config.config.get("retrieval_threshold") is not None:
            if builder.threshold != threshold:
                raise AppError(ErrorCode.VALIDATION_ERROR, "Retrieval configuration mismatch")
        return PipelineDependencies(
            assets.permissions,
            resolve,
            history,
            builder or ContextBuilder(ScopeOnlyIndex(), threshold=threshold),
            build_fixed_client(scripts[question.case_id], config.model_id),
            SqlGuard(),
            executor,
            querylog,
        )

    return build_eval_adapter(assets, dependencies)
