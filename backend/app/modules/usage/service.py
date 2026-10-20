import builtins
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.db import AsyncSession, Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.llm import CallContext, CallRecord
from app.core.response import Page, PageParams
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import QuotaInput, QuotaUsage, QuotaView, TrendPoint, UsageView


class UsageService:
    def __init__(self, repository: UsageRepository, audit: AuditService) -> None:
        self.repository, self.audit = repository, audit
        self.reservation: ContextVar[str | None] = ContextVar("usage_reservation", default=None)

    async def authorize_query(self, context: CallContext) -> None:
        """Reserve a daily question slot durably; model calls reserve only token capacity."""
        async with self.repository.accounting() as repository:
            await repository.admit_query(context)

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

    async def trend(
        self, actor: CurrentUser, by: str, since: date | None, until: date | None
    ) -> builtins.list[TrendPoint]:
        if not actor.active:
            raise PermissionDenied()
        if by not in {"team", "model"}:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        end = until or datetime.now(UTC).date()
        start = since or end - timedelta(days=29)
        if start > end or (end - start).days > 365:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        return [
            TrendPoint(date=day, key=key, calls=calls, tokens_in=tin, tokens_out=tout)
            for day, key, calls, tin, tout in await self.repository.trend(actor, by, start, end)
        ]

    async def quota_usage(self, actor: CurrentUser) -> builtins.list[QuotaUsage]:
        """Limits against consumption; 80% warns, 100% means new questions are being refused."""
        if not actor.active or actor.role == Role.MEMBER:
            raise PermissionDenied()
        today = datetime.now(UTC).date()
        tokens = await self.repository.month_tokens(today.replace(day=1))
        out: builtins.list[QuotaUsage] = []
        for quota in await self.repository.limited_quotas():
            if actor.role == Role.LEADER and quota.team_id != actor.team_id:
                continue
            checks = (
                ("monthly_tokens", quota.monthly_token_limit, tokens.get(quota.team_id, 0)),
                (
                    "daily_queries",
                    quota.daily_query_limit,
                    quota.daily_calls if quota.daily_date == today else 0,
                ),
            )
            for kind, limit, used in checks:
                if limit is None:
                    continue
                ratio = used / limit if limit else 1.0
                level = "exceeded" if ratio >= 1 else "warning" if ratio >= 0.8 else "ok"
                out.append(
                    QuotaUsage(
                        team_id=quota.team_id,
                        kind=kind,  # type: ignore[arg-type]
                        used=used,
                        limit=limit,
                        ratio=round(ratio, 4),
                        level=level,  # type: ignore[arg-type]
                    )
                )
        return sorted(out, key=lambda item: -item.ratio)


def build_service(session: "AsyncSession", database: "Database") -> UsageService:
    return UsageService(
        UsageRepository(session, database.sessions), AuditService(DatabaseAuditStore(session))
    )
