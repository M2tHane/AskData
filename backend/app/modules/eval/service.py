import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.datasource import Datasource
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.core.sql.executor import QueryLimits, QueryResult, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.core.tasks import TaskContext, TaskManager
from app.modules.eval.assets import EvalAssets
from app.modules.eval.models import EvalRun
from app.modules.eval.repository import EvalRepository
from app.modules.eval.schemas import (
    SAFETY_CATEGORIES,
    EvalCaseInput,
    EvalCaseView,
    EvalResultView,
    Evaluation,
    FeedbackCaseExpectation,
    FeedbackOrigin,
    FeedbackSnapshotInput,
    PipelineOutcome,
    RunComparison,
    RunListItem,
    RunRequest,
    RunView,
)
from app.modules.eval.scoring import compare_results, evaluate, summarize


class EvalQuestion(BaseModel):
    """Only input context crosses into chat: no candidate/reference SQL or expectations."""

    case_id: str
    datasource: str
    as_user: str
    question: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    clock: dict[str, Any] = Field(default_factory=dict)
    injection_context: dict[str, Any] | None = None


class ChatPipeline(Protocol):
    async def run(self, question: EvalQuestion, config: RunRequest) -> PipelineOutcome:
        """Call chat service as fixture identity. Instrument execution count from query logs.

        Must enforce PermissionService -> SqlGuard -> SqlExecutor. Adapters must
        validate chart config through the chat chart schema, and return only its verdict.
        """
        ...


class ReferenceRunner(Protocol):
    async def result(self, case: EvalCaseInput) -> QueryResult: ...


class ReferenceDatasourceProvider(Protocol):
    async def resolve(self, name: str) -> Datasource: ...


class FeedbackCaseSink:
    """Concrete feedback.service.EvaluationSink, using only the independent eval copy."""

    def __init__(
        self,
        repository: EvalRepository,
        assets: EvalAssets,
        permissions: PermissionService,
        audit: AuditService,
        guard: SqlGuard,
    ) -> None:
        self.repository, self.assets, self.permissions = repository, assets, permissions
        self.audit, self.guard = audit, guard

    @staticmethod
    def _values(expectation: FeedbackCaseExpectation) -> list[list[Any]] | None:
        if expectation.rows is not None and expectation.expected_values is not None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        values = expectation.expected_values
        if expectation.rows is not None:
            rows = expectation.rows
            columns = expectation.expected_columns or (list(rows[0]) if rows else [])
            if any(set(row) != set(columns) for row in rows):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            values = [[row[column] for column in columns] for row in rows]
            if columns:
                expectation.expected_columns = columns
        expected_columns = expectation.expected_columns
        if expected_columns is not None and (
            len(set(expected_columns)) != len(expected_columns)
            or any(not column.strip() for column in expected_columns)
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if values is not None:
            if len(values) > 10000 or any(len(row) > 100 or not row for row in values):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if values and any(len(row) != len(values[0]) for row in values):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if expected_columns is not None and any(
                len(row) != len(expected_columns) for row in values
            ):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if not values and expectation.expected_sql is None and expected_columns is None:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if values and expectation.expect_empty:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if expectation.result_mode == "scalar" and (len(values) != 1 or len(values[0]) != 1):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if not values and not expectation.expect_empty:
                raise AppError(ErrorCode.VALIDATION_ERROR)
        return values

    @staticmethod
    def _coherent(expectation: FeedbackCaseExpectation, values: list[list[Any]] | None) -> None:
        if expectation.category in SAFETY_CATEGORIES and expectation.expect_status != "blocked":
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if expectation.expect_status == "success":
            if (
                (expectation.expected_sql is None and values is None)
                or expectation.expect_code is not None
                or expectation.candidate_sql is not None
            ):
                raise AppError(ErrorCode.VALIDATION_ERROR)
        elif expectation.expected_sql is not None or values is not None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        elif expectation.expect_status == "blocked":
            allowed = {
                ErrorCode.SQL_NOT_SELECT,
                ErrorCode.SQL_MULTI_STATEMENT,
                ErrorCode.SQL_FORBIDDEN_FUNC,
                ErrorCode.SQL_SYSTEM_TABLE,
                ErrorCode.SQL_OUT_OF_SCOPE,
                ErrorCode.SQL_PARSE_FAILED,
                ErrorCode.SQL_COST_EXCEEDED,
            }
            if expectation.expect_code not in allowed or not expectation.candidate_sql:
                raise AppError(ErrorCode.VALIDATION_ERROR)
        elif (
            len(set(expectation.expected_clarification_options)) < 2
            or expectation.expect_code is not None
            or expectation.candidate_sql is not None
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if (
            (expectation.category == "chart" and not expectation.expected_chart_types)
            or (
                expectation.category == "semantic"
                and not (expectation.expected_semantic_ids or expectation.expected_metric_ids)
            )
            or (expectation.category == "time" and not expectation.expected_time_range)
            or (expectation.category == "empty" and not expectation.expect_empty)
            or (expectation.category == "clarify" and expectation.expect_status != "clarify")
            or (
                expectation.category == "out_of_scope"
                and expectation.expect_code != ErrorCode.SQL_OUT_OF_SCOPE
            )
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)

    async def create_case_from_feedback(
        self, snapshot: dict[str, Any], expected: dict[str, Any], *, actor: CurrentUser
    ) -> int:
        if not actor.active or actor.role not in {Role.ADMIN, Role.LEADER}:
            raise PermissionDenied()
        try:
            origin = FeedbackSnapshotInput.model_validate(snapshot)
            expectation = FeedbackCaseExpectation.model_validate(expected)
        except ValidationError:
            raise AppError(ErrorCode.VALIDATION_ERROR) from None
        if not origin.question.strip():
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if actor.role == Role.LEADER and (actor.team_id is None or origin.team_id != actor.team_id):
            raise PermissionDenied()
        users = [
            (alias, user) for alias, user in self.assets.users.items() if user.id == origin.user_id
        ]
        sources = [
            alias
            for alias, identifier in self.assets.datasource_ids.items()
            if identifier == origin.datasource_id
        ]
        if len(users) != 1 or len(sources) != 1:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, "Feedback replay bindings are not configured"
            )
        as_user, user = users[0]
        datasource = sources[0]
        if not user.active or user.team_id != origin.team_id:
            raise PermissionDenied()
        values = self._values(expectation)
        self._coherent(expectation, values)
        timezone = str(self.assets.fixtures["clock"]["timezone"])
        context = snapshot.get("context_snapshot")
        if isinstance(context, dict):
            ranges = context.get("time_ranges") or context.get("time_range")
            if isinstance(ranges, dict) and isinstance(ranges.get("timezone"), str):
                timezone = ranges["timezone"]
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise AppError(ErrorCode.VALIDATION_ERROR) from None
        async with self.repository.feedback_transaction():
            # Validate both the original query identity and the handling actor against
            # current scope. A leader cannot turn an employee query into an admin query.
            source_scope = await self.permissions.visible_scope(user, origin.datasource_id)
            actor_scope = await self.permissions.visible_scope(actor, origin.datasource_id)
            if not source_scope.tables or not actor_scope.tables:
                raise PermissionDenied()
            sql = expectation.expected_sql
            if sql is not None:
                dialect = self.assets.fixtures["datasources"][datasource]["dialect"]
                self.guard.check(sql, dialect, actor_scope)
                sql = self.guard.check(sql, dialect, source_scope).normalized_sql
            definition = expectation.model_dump(exclude={"rows"})
            definition.update(
                id="feedback_" + uuid4().hex,
                datasource=datasource,
                as_user=as_user,
                question=origin.question,
                source="feedback",
                expected_sql=sql,
                expected_values=values,
                feedback_origin=FeedbackOrigin(
                    query_run_id=origin.id,
                    query_run_created_at=origin.created_at,
                    datasource_id=origin.datasource_id,
                    user_id=origin.user_id,
                    team_id=origin.team_id,
                    converted_by=actor.id,
                    timezone=timezone,
                ),
            )
            try:
                case = EvalCaseInput.model_validate(definition)
            except ValidationError:
                raise AppError(ErrorCode.VALIDATION_ERROR) from None
            row = await self.repository.save_case(case, actor.id)
            await self.audit.record(
                actor,
                "eval.case.from_feedback",
                ("eval_case", str(row.id)),
                {"query_run_id": origin.id, "category": case.category, "source": "feedback"},
            )
            return row.id

    async def create_from_feedback(
        self, snapshot: dict[str, Any], expected: dict[str, Any], *, actor: CurrentUser
    ) -> int:
        """Keep the exact existing G1 EvaluationSink method name and signature."""
        return await self.create_case_from_feedback(snapshot, expected, actor=actor)


def build_feedback_sink(
    session: AsyncSession,
    assets: EvalAssets,
    permissions: PermissionService | None = None,
    *,
    audit: AuditService | None = None,
    guard: SqlGuard | None = None,
) -> FeedbackCaseSink:
    return FeedbackCaseSink(
        EvalRepository(session),
        assets,
        permissions if permissions is not None else assets.permissions,
        audit or AuditService(DatabaseAuditStore(session)),
        guard or SqlGuard(),
    )


def expected_values_result(
    case: EvalCaseInput, reference_columns: list[str] | None = None
) -> QueryResult:
    """Explicit human-authored expectations are data, never SQL or model context."""
    values = case.expected_values
    if values is None or case.expect_status != "success":
        raise AppError(ErrorCode.VALIDATION_ERROR)
    columns = case.expected_columns or reference_columns
    width = len(columns) if columns else (len(values[0]) if values else 0)
    if not width or any(len(row) != width for row in values):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    if columns is None:
        columns = [f"column_{index}" for index in range(width)]
    return QueryResult(
        columns=columns,
        rows=[dict(zip(columns, row, strict=True)) for row in values],
        row_count=len(values),
        truncated=False,
        duration_ms=0,
    )


def case_clock(case: EvalCaseInput, baseline: dict[str, Any]) -> dict[str, Any]:
    origin = case.feedback_origin
    if case.source == "feedback" and origin and origin.query_run_created_at is not None:
        now = origin.query_run_created_at
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return {**baseline, "now": now.isoformat(), "timezone": origin.timezone}
    return dict(baseline)


class GuardedReferenceRunner:
    def __init__(
        self,
        assets: EvalAssets,
        datasources: ReferenceDatasourceProvider,
        permissions: PermissionService,
        guard: SqlGuard,
        executor: SqlExecutor,
        sql_mapper: Callable[[str, str], str] | None = None,
    ) -> None:
        self.assets, self.datasources, self.permissions = assets, datasources, permissions
        self.guard, self.executor = guard, executor
        self.sql_mapper = sql_mapper

    async def result(self, case: EvalCaseInput) -> QueryResult:
        if case.expect_status != "success" or (
            case.expected_sql is None and case.expected_values is None
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        datasource = await self.datasources.resolve(case.datasource)
        user = self.assets.users[case.as_user]
        scope = await self.permissions.visible_scope(user, datasource.id)
        if case.expected_sql is None:
            if not scope.tables:
                raise PermissionDenied()
            return expected_values_result(case)
        sql = (
            self.sql_mapper(case.datasource, case.expected_sql)
            if self.sql_mapper is not None
            else case.expected_sql
        )
        guarded = self.guard.check(sql, datasource.dialect, scope)
        result = await self.executor.run(datasource, guarded, QueryLimits(user_id=user.id))
        if case.source == "feedback" and case.expected_values is not None:
            expected = expected_values_result(case, result.columns)
            if not compare_results(expected, result, case.result_mode):
                raise AppError(ErrorCode.VALIDATION_ERROR, "Feedback reference result mismatch")
        return result


class EvalService:
    def __init__(
        self,
        repository: EvalRepository,
        assets: EvalAssets,
        pipeline: ChatPipeline,
        references: ReferenceRunner,
        audit: AuditService,
        tasks: TaskManager,
    ) -> None:
        self.repository, self.assets, self.pipeline = repository, assets, pipeline
        self.references, self.audit, self.tasks = references, audit, tasks

    @staticmethod
    def _role(actor: CurrentUser, write: bool = False) -> None:
        roles = {Role.ADMIN} if write else {Role.ADMIN, Role.LEADER}
        if not actor.active or actor.role not in roles:
            raise PermissionDenied()

    async def create_case_from_feedback(
        self, snapshot: dict[str, Any], expected: dict[str, Any], *, actor: CurrentUser
    ) -> int:
        return await FeedbackCaseSink(
            self.repository, self.assets, self.assets.permissions, self.audit, SqlGuard()
        ).create_case_from_feedback(snapshot, expected, actor=actor)

    async def create_from_feedback(
        self, snapshot: dict[str, Any], expected: dict[str, Any], *, actor: CurrentUser
    ) -> int:
        return await self.create_case_from_feedback(snapshot, expected, actor=actor)

    async def save_case(self, case: EvalCaseInput, *, actor: CurrentUser) -> EvalCaseInput:
        self._role(actor, write=True)
        # No malicious candidate SQL may become a reference execution.
        if case.expect_status != "success" and case.expected_sql is not None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if (
            case.as_user not in self.assets.users
            or case.datasource not in self.assets.datasource_ids
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        async with self.repository.transaction():
            row = await self.repository.save_case(case, actor.id)
            await self.audit.record(actor, "eval.case.save", ("eval_case", str(row.id)))
        return case

    async def load_cases(self, *, actor: CurrentUser) -> int:
        self._role(actor, write=True)
        async with self.repository.transaction():
            for case in self.assets.cases:
                await self.repository.save_case(case, actor.id)
            await self.audit.record(
                actor,
                "eval.assets.load",
                ("eval_case", "baseline"),
                {"count": len(self.assets.cases)},
            )
        return len(self.assets.cases)

    async def start(self, request: RunRequest, *, actor: CurrentUser) -> RunView:
        self._role(actor, write=True)
        async with self.repository.transaction():
            cases = await self.repository.all_cases()
            selected = [
                c for c in cases if not request.case_ids or c.external_id in request.case_ids
            ]
            if not selected or set(request.case_ids) - {c.external_id for c in selected}:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            row = await self.repository.runs.create(
                {
                    "model_id": request.model_id,
                    "note": request.note.strip(),
                    "prompt_versions": request.prompt_versions,
                    "config": request.config,
                    "status": "queued",
                    "summary": {},
                    "case_snapshot": [
                        {"case_id": c.id, "definition": c.definition} for c in selected
                    ],
                },
                actor.id,
            )
            identifier = row.id
            await self.audit.record(actor, "eval.run.start", ("eval_run", str(identifier)))
        return await self._enqueue(identifier, actor)

    async def _enqueue(self, identifier: int, actor: CurrentUser) -> RunView:
        """Queue a worker for a queued run. The worker claims the run by its task id."""
        try:
            task = await self.tasks.enqueue(
                "eval.run",
                {
                    "run_id": identifier,
                    "actor": actor.model_dump(mode="json"),
                },
            )
        except AppError:
            async with self.repository.transaction():
                row = await self.repository.required_run(identifier, lock=True)
                await self.repository.runs.update(row, {"status": "failed"})
            raise AppError(ErrorCode.TASK_FAILED, http_status=503) from None
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier, lock=True)
            # The worker may already have claimed it; never take the run back from it.
            if row.task_id is None:
                await self.repository.runs.update(row, {"task_id": task.id})
        return await self.get(identifier, actor=actor)

    async def _owned(self, identifier: int, task_id: str) -> EvalRun | None:
        """The locked run while this task still owns it; None once stopped, resumed by
        another task, or deleted. Must be called inside a transaction."""
        row = await self.repository.find_run(identifier, lock=True)
        if row is None or row.status != "running" or row.task_id != task_id:
            return None
        return row

    async def run(self, context: TaskContext) -> None:
        identifier = int(context.task.payload["run_id"])
        actor = CurrentUser.model_validate(context.task.payload["actor"])
        self._role(actor, write=True)
        async with self.repository.transaction():
            row = await self.repository.find_run(identifier, lock=True)
            # Stopped or deleted before a worker got to it, or already taken by a newer task.
            if row is None or row.status == "canceled":
                return
            # A redelivered task (worker restarted mid-run) may continue its own run.
            redelivered = row.status == "running" and row.task_id == context.task.id
            if not redelivered and (
                row.status != "queued" or row.task_id not in {None, context.task.id}
            ):
                raise AppError(ErrorCode.CONFLICT)
            await self.repository.runs.update(
                row,
                {
                    "status": "running",
                    "task_id": context.task.id,
                    "started_at": datetime.now(UTC),
                    "finished_at": None,
                },
            )
            snapshots = row.case_snapshot
            request = RunRequest(
                model_id=row.model_id, prompt_versions=row.prompt_versions, config=row.config
            )
            # A resumed run continues where it stopped.
            scored = await self.repository.scored_case_ids(identifier)
            results = await self.repository.results(identifier)
        try:
            for index, snapshot in enumerate(snapshots):
                if snapshot["case_id"] in scored:
                    continue
                case = EvalCaseInput.model_validate(snapshot["definition"])
                try:
                    # Self-check references before scoring; safety/clarify never execute one.
                    reference = (
                        await self.references.result(case)
                        if case.expect_status == "success"
                        else None
                    )
                    question = EvalQuestion(
                        case_id=case.id,
                        datasource=case.datasource,
                        as_user=case.as_user,
                        question=case.question,
                        history=case.history,
                        clock=case_clock(case, self.assets.fixtures["clock"]),
                        injection_context=case.injection_context,
                    )
                    outcome = await self.pipeline.run(question, request)
                    result = evaluate(
                        case,
                        outcome,
                        reference,
                        await self.assets.hidden_names(case),
                        exact_code=False,
                    )
                except AppError as error:
                    current = asyncio.current_task()
                    if (
                        error.code == ErrorCode.QUERY_CANCELLED
                        and current is not None
                        and current.cancelling()
                    ):
                        # The worker itself is being stopped (job timeout, shutdown) mid-question.
                        # The pipeline reports that as an error; scoring it would record a failure
                        # the model never made, and a resume would skip it. Stop the run here and
                        # leave the question unanswered.
                        raise
                    result = Evaluation(
                        case_id=case.id,
                        category=case.category,
                        passed=False,
                        reason="execution_failed",
                        error=error.code.value,
                    )
                async with self.repository.transaction():
                    # Locked so a stop request sees either all or none of this question.
                    if await self._owned(identifier, context.task.id) is None:
                        return
                    await self.repository.save_result(identifier, snapshot["case_id"], result)
                results.append(result)
                await context.progress((index + 1) * 100 // len(snapshots))
        except (Exception, asyncio.CancelledError) as error:
            async with self.repository.transaction():
                row = await self._owned(identifier, context.task.id)
                if row is not None:
                    await self.repository.runs.update(
                        row,
                        {
                            "status": "failed",
                            "finished_at": datetime.now(UTC),
                            "summary": summarize(results),
                        },
                    )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AppError(ErrorCode.TASK_FAILED) from None
        async with self.repository.transaction():
            row = await self._owned(identifier, context.task.id)
            if row is None:
                return
            await self.repository.runs.update(
                row,
                {
                    "status": "success",
                    "finished_at": datetime.now(UTC),
                    "summary": summarize(results),
                },
            )
            await self.audit.record(actor, "eval.run.finish", ("eval_run", str(identifier)))

    async def cancel(self, identifier: int, *, actor: CurrentUser) -> RunView:
        """Stop a queued or running run. Works even when its worker is already gone."""
        self._role(actor, write=True)
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier, lock=True)
            if row.status not in {"queued", "running"}:
                raise AppError(ErrorCode.CONFLICT)
            results = await self.repository.results(identifier)
            # The worker checks this status before saving each answer and then stops.
            await self.repository.runs.update(
                row,
                {
                    "status": "canceled",
                    "finished_at": datetime.now(UTC),
                    "summary": summarize(results),
                },
            )
            await self.audit.record(actor, "eval.run.cancel", ("eval_run", str(identifier)))
        return await self.get(identifier, actor=actor)

    async def resume(self, identifier: int, *, actor: CurrentUser) -> RunView:
        """Continue a stopped or failed run with the questions it has not answered yet."""
        self._role(actor, write=True)
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier, lock=True)
            if row.status not in {"canceled", "failed"}:
                raise AppError(ErrorCode.CONFLICT)
            if await self.repository.result_count(identifier) >= len(row.case_snapshot):
                raise AppError(ErrorCode.CONFLICT, "Every question is already answered")
            # Clearing the owner makes a stopped worker still finishing a question drop it.
            await self.repository.runs.update(
                row, {"status": "queued", "task_id": None, "finished_at": None}
            )
            await self.audit.record(actor, "eval.run.resume", ("eval_run", str(identifier)))
        return await self._enqueue(identifier, actor)

    async def delete(self, identifier: int, *, actor: CurrentUser) -> None:
        self._role(actor, write=True)
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier, lock=True)
            if row.status in {"queued", "running"}:
                raise AppError(ErrorCode.CONFLICT, "Stop the run before deleting it")
            await self.repository.delete_run(row)
            await self.audit.record(actor, "eval.run.delete", ("eval_run", str(identifier)))

    @staticmethod
    def case_view(case: EvalCaseInput, actor: CurrentUser) -> EvalCaseView:
        return EvalCaseView(
            id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            question=case.question,
            as_user=case.as_user,
            expect_status=case.expect_status,
            expect_code=case.expect_code.value if case.expect_code else None,
            # Reference SQL is withheld from leaders like the actual SQL is.
            expected_sql=case.expected_sql if actor.role == Role.ADMIN else None,
            history=case.history,
            result_mode=case.result_mode,
            expect_empty=case.expect_empty,
            expected_clarification_options=case.expected_clarification_options,
            accept_clarify=case.accept_clarify,
            require_assumption=case.require_assumption,
            accept_safe_answer=case.accept_safe_answer,
        )

    async def cases(self, *, actor: CurrentUser) -> list[EvalCaseView]:
        """The loaded question bank, so reviewers can see what a run will ask."""
        self._role(actor)
        async with self.repository.transaction():
            rows = await self.repository.all_cases()
        return [self.case_view(EvalCaseInput.model_validate(row.definition), actor) for row in rows]

    async def runs(self, *, actor: CurrentUser) -> list[RunListItem]:
        self._role(actor)
        async with self.repository.transaction():
            rows = await self.repository.runs_page()
            progress = await self.repository.progress([row.id for row in rows])
            return [
                RunListItem(
                    id=row.id,
                    model_id=row.model_id,
                    note=row.note,
                    status=row.status,
                    created_at=row.created_at,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    case_count=len(row.case_snapshot),
                    done=progress.get(row.id, (0, 0, 0))[0],
                    passed=progress.get(row.id, (0, 0, 0))[1],
                    duration_ms=progress.get(row.id, (0, 0, 0))[2],
                )
                for row in rows
            ]

    async def get(self, identifier: int, *, actor: CurrentUser) -> RunView:
        self._role(actor)
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier)
            view = RunView.model_validate(row)
            view.case_ids = [str(item["definition"]["id"]) for item in row.case_snapshot]
            view.case_count = len(row.case_snapshot)
            view.done = await self.repository.result_count(identifier)
            return view

    async def results(self, identifier: int, *, actor: CurrentUser) -> list[EvalResultView]:
        self._role(actor)
        async with self.repository.transaction():
            run = await self.repository.required_run(identifier)
            results = await self.repository.results(identifier)
        # Questions come from the run's own snapshot: later edits to the bank don't rewrite
        # what this run asked.
        asked = {
            case.id: case
            for case in (
                EvalCaseInput.model_validate(item["definition"]) for item in run.case_snapshot
            )
        }
        order = {case_id: index for index, case_id in enumerate(asked)}
        views = []
        for result in sorted(results, key=lambda r: order.get(r.case_id, len(order))):
            hidden: dict[str, Any] = {
                "actual_sql": None,
                "actual_rows": [],
                "expected_rows": [],
            }
            view = EvalResultView(
                **result.model_dump(),
                case=self.case_view(asked[result.case_id], actor)
                if result.case_id in asked
                else None,
            )
            views.append(view.model_copy(update=hidden) if actor.role == Role.LEADER else view)
        return views

    async def compare(self, before: int, after: int, *, actor: CurrentUser) -> RunComparison:
        old = {r.case_id: r.passed for r in await self.results(before, actor=actor)}
        new = {r.case_id: r.passed for r in await self.results(after, actor=actor)}
        common = old.keys() & new.keys()
        return RunComparison(
            before_id=before,
            after_id=after,
            new_failures=sorted(c for c in common if old[c] and not new[c]),
            fixed=sorted(c for c in common if not old[c] and new[c]),
            added=sorted(new.keys() - old.keys()),
            removed=sorted(old.keys() - new.keys()),
        )
