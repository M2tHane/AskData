from __future__ import annotations

from builtins import list as List
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.db import AsyncSession, Base
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.core.response import Page, PageParams
from app.core.retention import DatabaseRetentionStore
from app.core.scope import ScopedQuery, ScopeLevel
from app.core.sql.guard import SqlGuard
from app.modules.embedding.service import build_outbox
from app.modules.feedback.models import Feedback
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.schemas import (
    ConversionInput,
    ExampleInput,
    ExampleReview,
    ExampleView,
    FeedbackInput,
    FeedbackView,
    MyFeedback,
)
from app.modules.metric.service import DatasourceResolver
from app.modules.querylog.service import QueryLogService
from app.modules.semantic.service import IndexPublisher


class EvaluationSink(Protocol):
    """Public eval service port, implemented by I2 without importing eval repositories here.

    Authorize the real actor (admin or the feedback team's leader), persist an independent
    draft with source=feedback, audit it, and return its positive ID. Use the caller's session
    without committing: draft, converted_ref and both audits must succeed or roll back together.
    Copy the masked snapshot/expected data; the draft must survive deletion of the source log.
    """

    async def create_from_feedback(
        self, snapshot: dict[str, Any], expected: dict[str, Any], *, actor: CurrentUser
    ) -> int: ...


class FeedbackService:
    def __init__(
        self,
        repository: FeedbackRepository,
        queries: QueryLogService,
        permissions: PermissionService,
        datasources: DatasourceResolver,
        guard: SqlGuard,
        audit: AuditService,
        index: IndexPublisher,
        evaluations: EvaluationSink | None,
    ) -> None:
        self.repository, self.queries = repository, queries
        self.permissions, self.datasources, self.guard = permissions, datasources, guard
        self.audit, self.index, self.evaluations = audit, index, evaluations

    async def submit(self, data: FeedbackInput, *, actor: CurrentUser) -> FeedbackView:
        async with self.repository.atomic():
            query = await self.queries.get(data.query_run_id, actor)
            if query.user_id != actor.id:
                raise PermissionDenied()
            existing = await self.repository.own(data.query_run_id, actor.id)
            if existing is not None:
                # Changing one's mind updates the same feedback and puts it back in the queue.
                await self.repository.update(
                    existing,
                    {**data.model_dump(), "status": "open", "handled_by": None, "handled_at": None},
                )
                await self.audit.record(actor, "feedback.update", ("feedback", str(existing.id)))
                return FeedbackView.model_validate(existing)
            # QueryView contains only the masked preview emitted by the core writer.
            row = await self.repository.create(
                {
                    **data.model_dump(),
                    "user_id": actor.id,
                    "team_id": actor.team_id,
                    "query_run_created_at": query.created_at,
                    "snapshot": query.model_dump(mode="json"),
                    "status": "open",
                    "handled_by": None,
                    "handled_at": None,
                    "converted_ref": None,
                    "expires_at": query.expires_at,
                },
                actor.id,
            )
            await self.queries.mark_feedback(query.id, actor=actor)
            await self.audit.record(actor, "feedback.submit", ("feedback", str(row.id)))
            return FeedbackView.model_validate(row)

    async def mine(self, query_run_ids: List[int], *, actor: CurrentUser) -> List[MyFeedback]:
        if not actor.active or len(query_run_ids) > 200:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if not query_run_ids:
            return []
        latest = {
            row.query_run_id: row for row in await self.repository.mine(query_run_ids, actor.id)
        }
        return [MyFeedback.model_validate(row) for row in latest.values()]

    async def list(
        self,
        actor: CurrentUser,
        page: PageParams,
        status: str | None = None,
        *,
        user_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Page[FeedbackView]:
        if not actor.active:
            raise PermissionDenied()
        if status is not None and status not in {"open", "handled"}:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if since and until and since >= until:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        rows, total = await self.repository.search(
            actor, page, status, user_id=user_id, since=since, until=until
        )
        return Page(
            items=[FeedbackView.model_validate(row) for row in rows],
            total=total,
            page=page.page,
            page_size=page.page_size,
        )

    async def _handling(self, identifier: int, actor: CurrentUser) -> Any:
        row = await self.repository.locked(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        expires = (
            row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
        )
        if expires <= datetime.now(UTC):
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if (
            not actor.active
            or actor.role == Role.MEMBER
            or (actor.role == Role.LEADER and row.team_id != actor.team_id)
        ):
            raise PermissionDenied()
        return row

    async def handle(self, identifier: int, *, actor: CurrentUser) -> FeedbackView:
        async with self.repository.atomic():
            row = await self._handling(identifier, actor)
            await self.repository.update(
                row, {"status": "handled", "handled_by": actor.id, "handled_at": datetime.now(UTC)}
            )
            await self.audit.record(actor, "feedback.handle", ("feedback", str(identifier)))
            return FeedbackView.model_validate(row)

    async def convert(
        self, identifier: int, data: ConversionInput, *, actor: CurrentUser
    ) -> FeedbackView:
        async with self.repository.atomic():
            row = await self._handling(identifier, actor)
            if row.converted_ref is not None:
                raise AppError(ErrorCode.CONFLICT)
            if data.kind == "eval":
                if self.evaluations is None:
                    raise AppError(ErrorCode.TASK_FAILED, http_status=503)
                if not data.expected:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                ref = await self.evaluations.create_from_feedback(
                    deepcopy(row.snapshot), deepcopy(data.expected), actor=actor
                )
                if type(ref) is not int or ref < 1:
                    raise AppError(ErrorCode.TASK_FAILED)
            else:
                snapshot = row.snapshot
                sql = data.corrected_sql or snapshot.get("normalized_sql")
                if not sql or not snapshot.get("question"):
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                example = await self.create_example(
                    ExampleInput(
                        datasource_id=snapshot["datasource_id"],
                        question=snapshot["question"],
                        sql=sql,
                        scope=ScopeLevel.PLATFORM if actor.role == Role.ADMIN else ScopeLevel.TEAM,
                    ),
                    actor=actor,
                    source="feedback",
                )
                ref = example.id
            await self.repository.update(
                row,
                {
                    "converted_ref": f"{data.kind}:{ref}",
                    "example_id": ref if data.kind == "example" else None,
                    "status": "handled",
                    "handled_by": actor.id,
                    "handled_at": datetime.now(UTC),
                },
            )
            await self.audit.record(
                actor,
                "feedback.convert",
                ("feedback", str(identifier)),
                {"kind": data.kind, "ref_id": ref},
            )
            return FeedbackView.model_validate(row)

    async def _checked(self, data: ExampleInput, actor: CurrentUser) -> str:
        scope = await self.permissions.visible_scope(actor, data.datasource_id)
        datasource = await self.datasources.resolve(data.datasource_id, actor)
        return self.guard.check(data.sql, datasource.dialect, scope).normalized_sql

    async def create_example(
        self, data: ExampleInput, *, actor: CurrentUser, source: str = "manual"
    ) -> ExampleView:
        if not actor.active or source not in {"manual", "feedback"}:
            raise PermissionDenied()
        if (
            (actor.role == Role.ADMIN and data.scope != ScopeLevel.PLATFORM)
            or (actor.role == Role.LEADER and data.scope != ScopeLevel.TEAM)
            or actor.role == Role.MEMBER
        ):
            raise PermissionDenied()
        async with self.repository.atomic():
            sql = await self._checked(data, actor)
            row = await self.repository.examples.create(
                {
                    **data.model_dump(),
                    "sql": sql,
                    "source": source,
                    "approved": False,
                    "version": 1,
                    "status": "active",
                    "owner_team_id": actor.team_id if data.scope == ScopeLevel.TEAM else None,
                    "owner_user_id": None,
                    "review_comment": "",
                },
                actor.id,
            )
            await self.audit.record(actor, "example.create", ("example_sql", str(row.id)))
            return ExampleView.model_validate(row)

    async def review_example(
        self, identifier: int, data: ExampleReview, *, actor: CurrentUser
    ) -> ExampleView:
        async with self.repository.atomic():
            row = await self.repository.locked_example(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            if not actor.active or not (
                (actor.role == Role.ADMIN and row.scope == "platform")
                or (
                    actor.role == Role.LEADER
                    and row.scope == "team"
                    and row.owner_team_id == actor.team_id
                )
            ):
                raise PermissionDenied()
            if not data.approved and not data.comment.strip():
                raise AppError(ErrorCode.VALIDATION_ERROR)
            before = ExampleView.model_validate(row)
            sql = await self._checked(before, actor)
            await self.repository.examples.update(
                row,
                {
                    "approved": data.approved,
                    "sql": sql,
                    "review_comment": data.comment,
                    "version": row.version + 1,
                },
            )
            await self.audit.record(
                actor,
                "example.review",
                ("example_sql", str(identifier)),
                {"before": before.model_dump(mode="json"), **data.model_dump()},
            )
            await self.index.mark_dirty("example", row.id, row.datasource_id, actor=actor)
            return ExampleView.model_validate(row)

    async def examples(
        self, actor: CurrentUser, datasource_id: int, *, approved_only: bool = True
    ) -> List[ExampleView]:
        await self.permissions.visible_scope(actor, datasource_id)
        items = ScopedQuery.visible_to(
            [
                ExampleView.model_validate(row)
                for row in await self.repository.example_candidates(datasource_id)
            ],
            actor,
        )
        result = []
        for item in items:
            if not item.approved and (approved_only or actor.role == Role.MEMBER):
                continue
            try:
                await self._checked(item, actor)
            except AppError as error:
                if error.code in {ErrorCode.FORBIDDEN, ErrorCode.SQL_OUT_OF_SCOPE}:
                    continue
                raise
            result.append(item)
        return result


def build_retention_store(session: AsyncSession) -> DatabaseRetentionStore:
    """Compose with core log retention in one transaction.

    Snapshots inherit query_run.expires_at; its composite FK also cascades source deletions.
    Eval conversion preserves an independent draft through EvaluationSink, not the expired
    feedback row. This cleaner owns only feedback and never deletes eval cases/examples.
    """
    return DatabaseRetentionStore(session, [Base.metadata.tables[Feedback.__tablename__]])


def build_service(
    session: AsyncSession,
    queries: QueryLogService,
    permissions: PermissionService,
    datasources: DatasourceResolver,
    evaluations: EvaluationSink | None = None,
) -> FeedbackService:
    return FeedbackService(
        FeedbackRepository(session),
        queries,
        permissions,
        datasources,
        SqlGuard(),
        AuditService(DatabaseAuditStore(session)),
        build_outbox(session),
        evaluations,
    )


async def index_catalog(service: FeedbackService, datasource_id: int) -> list[tuple[int, int]]:
    return [
        (row.id, row.created_by)
        for row in await service.repository.example_candidates(datasource_id)
        if row.approved and row.created_by is not None
    ]
