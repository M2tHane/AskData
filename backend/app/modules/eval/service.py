import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.audit import AuditService
from app.core.datasource import Datasource
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.core.sql.executor import QueryLimits, QueryResult, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.core.tasks import TaskContext, TaskManager
from app.modules.eval.assets import EvalAssets
from app.modules.eval.repository import EvalRepository
from app.modules.eval.schemas import (
    EvalCaseInput,
    Evaluation,
    PipelineOutcome,
    RunComparison,
    RunRequest,
    RunView,
)
from app.modules.eval.scoring import evaluate, summarize


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
        if case.expect_status != "success" or case.expected_sql is None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        datasource = await self.datasources.resolve(case.datasource)
        user = self.assets.users[case.as_user]
        scope = await self.permissions.visible_scope(user, datasource.id)
        sql = (
            self.sql_mapper(case.datasource, case.expected_sql)
            if self.sql_mapper is not None
            else case.expected_sql
        )
        guarded = self.guard.check(sql, datasource.dialect, scope)
        return await self.executor.run(datasource, guarded, QueryLimits(user_id=user.id))


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
            await self.repository.runs.update(row, {"task_id": task.id})
            return RunView.model_validate(row)

    async def run(self, context: TaskContext) -> None:
        identifier = int(context.task.payload["run_id"])
        actor = CurrentUser.model_validate(context.task.payload["actor"])
        self._role(actor, write=True)
        async with self.repository.transaction():
            row = await self.repository.required_run(identifier, lock=True)
            if row.status != "queued":
                raise AppError(ErrorCode.CONFLICT)
            await self.repository.runs.update(
                row, {"status": "running", "started_at": datetime.now(UTC)}
            )
            snapshots = row.case_snapshot
            request = RunRequest(
                model_id=row.model_id, prompt_versions=row.prompt_versions, config=row.config
            )
        results = []
        try:
            for index, snapshot in enumerate(snapshots):
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
                        clock=self.assets.fixtures["clock"],
                        injection_context=case.injection_context,
                    )
                    outcome = await self.pipeline.run(question, request)
                    result = evaluate(
                        case, outcome, reference, await self.assets.hidden_names(case)
                    )
                except AppError:
                    result = Evaluation(
                        case_id=case.id,
                        category=case.category,
                        passed=False,
                        reason="execution_failed",
                    )
                async with self.repository.transaction():
                    await self.repository.save_result(identifier, snapshot["case_id"], result)
                results.append(result)
                await context.progress((index + 1) * 100 // len(snapshots))
        except (Exception, asyncio.CancelledError) as error:
            async with self.repository.transaction():
                row = await self.repository.required_run(identifier, lock=True)
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
            row = await self.repository.required_run(identifier, lock=True)
            await self.repository.runs.update(
                row,
                {
                    "status": "success",
                    "finished_at": datetime.now(UTC),
                    "summary": summarize(results),
                },
            )
            await self.audit.record(actor, "eval.run.finish", ("eval_run", str(identifier)))

    async def get(self, identifier: int, *, actor: CurrentUser) -> RunView:
        self._role(actor)
        async with self.repository.transaction():
            return RunView.model_validate(await self.repository.required_run(identifier))

    async def results(self, identifier: int, *, actor: CurrentUser) -> list[Evaluation]:
        self._role(actor)
        async with self.repository.transaction():
            await self.repository.required_run(identifier)
            results = await self.repository.results(identifier)
            if actor.role == Role.LEADER:
                results = [r.model_copy(update={"actual_sql": None}) for r in results]
            return results

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
