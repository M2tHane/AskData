from contextvars import ContextVar
from datetime import date
from uuid import uuid4

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.db import AsyncSession, Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.llm import CallContext, CallRecord
from app.core.response import Page, PageParams
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import QuotaInput, QuotaView, UsageView


class UsageService:
    def __init__(self, repository: UsageRepository, audit: AuditService) -> None:
        self.repository, self.audit = repository, audit
        self.reservation: ContextVar[str | None] = ContextVar("usage_reservation", default=None)

    async def authorize(self, context: CallContext) -> None:
        identifier = str(uuid4())
        async with self.repository.accounting() as repository:
            await repository.reserve(context, identifier)
        self.reservation.set(identifier if context.team_id is not None else None)

    async def record(self, record: CallRecord) -> None:
        identifier = self.reservation.get()
        async with self.repository.accounting() as repository:
            await repository.record(record, identifier)
        # Retries record additional tokens but settle the one authorization only once.
        self.reservation.set(None)

    async def set_quota(self, team_id: int, data: QuotaInput, *, actor: CurrentUser) -> QuotaView:
        if not actor.active or actor.role != Role.ADMIN:
            raise PermissionDenied()
        if team_id < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        async with self.repository.atomic():
            old = QuotaView.model_validate(await self.repository.quota(team_id, True))
            row = await self.repository.set_quota(team_id, data)
            await self.audit.record(
                actor,
                "usage.quota",
                ("team_quota", str(team_id)),
                {"before": old.model_dump(), "after": data.model_dump()},
            )
            return QuotaView.model_validate(row)

    async def get_quota(self, team_id: int, actor: CurrentUser) -> QuotaView:
        if (
            not actor.active
            or actor.role == Role.MEMBER
            or (actor.role == Role.LEADER and actor.team_id != team_id)
        ):
            raise PermissionDenied()
        return QuotaView.model_validate(await self.repository.quota(team_id))

    async def list(
        self,
        actor: CurrentUser,
        page: PageParams,
        since: date | None = None,
        until: date | None = None,
    ) -> Page[UsageView]:
        if not actor.active:
            raise PermissionDenied()
        if since and until and since > until:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        rows, total = await self.repository.search(actor, page, since, until)
        return Page(
            items=[UsageView.model_validate(row) for row in rows],
            total=total,
            page=page.page,
            page_size=page.page_size,
        )


def build_service(session: "AsyncSession", database: "Database") -> UsageService:
    return UsageService(
        UsageRepository(session, database.sessions), AuditService(DatabaseAuditStore(session))
    )
