from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, CallRecord
from app.core.response import PageParams
from app.modules.usage.models import TeamQuota, UsageDaily, UsageQuery, UsageReservation
from app.modules.usage.schemas import QuotaInput


class UsageRepository:
    def __init__(self, session: AsyncSession, sessions: Callable[[], AsyncSession]) -> None:
        self.session, self.sessions = session, sessions

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    @asynccontextmanager
    async def accounting(self) -> AsyncIterator["UsageRepository"]:
        # Provider callbacks commit independently; failed queries must still consume their usage.
        async with self.sessions() as session, session.begin():
            yield UsageRepository(session, self.sessions)

    async def quota(self, team_id: int, lock: bool = False) -> TeamQuota:
        if lock:
            insert = (
                pg_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
            )
            await self.session.execute(
                insert(TeamQuota)
                .values(team_id=team_id, daily_calls=0)
                .on_conflict_do_nothing(index_elements=["team_id"])
            )
        query = select(TeamQuota).where(TeamQuota.team_id == team_id)
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query)
        if row is None:
            if not lock:
                return TeamQuota(team_id=team_id, daily_calls=0)
            raise AppError(ErrorCode.INTERNAL_ERROR)
        return row

    async def set_quota(self, team_id: int, data: QuotaInput) -> TeamQuota:
        row = await self.quota(team_id, True)
        return await BaseRepository(self.session, TeamQuota).update(row, data.model_dump())

    async def admit_query(self, context: CallContext, quota: TeamQuota | None = None) -> None:
        if context.team_id is None or context.query_id is None:
            return
        quota = quota or await self.quota(context.team_id, True)
        receipt = await self.session.scalar(
            select(UsageQuery).where(
                UsageQuery.team_id == context.team_id, UsageQuery.query_id == context.query_id
            )
        )
        if receipt is not None:
            if receipt.user_id != context.user_id:
                raise AppError(ErrorCode.CONFLICT)
            return
        today = datetime.now(UTC).date()
        if quota.daily_date != today:
            quota.daily_date, quota.daily_calls = today, 0
        if quota.daily_query_limit is not None and quota.daily_calls >= quota.daily_query_limit:
            raise AppError(ErrorCode.QUOTA_EXCEEDED, http_status=429)
        quota.daily_calls += 1
        self.session.add(
            UsageQuery(
                query_id=context.query_id,
                team_id=context.team_id,
                user_id=context.user_id,
                date=today,
                created_by=context.user_id,
            )
        )
        await self.session.flush()

    async def reserve(self, context: CallContext, reservation_id: str) -> None:
        if context.team_id is None:
            return
        quota = await self.quota(context.team_id, True)
        await self.admit_query(context, quota)
        now = datetime.now(UTC)
        today = now.date()
        tokens = (
            await self.session.scalar(
                select(
                    func.coalesce(func.sum(UsageDaily.tokens_in + UsageDaily.tokens_out), 0)
                ).where(
                    UsageDaily.team_id == context.team_id, UsageDaily.date >= today.replace(day=1)
                )
            )
            or 0
        )
        await self.session.execute(
            delete(UsageReservation).where(UsageReservation.expires_at <= now)
        )
        pending = (
            await self.session.scalar(
                select(func.count())
                .select_from(UsageReservation)
                .where(UsageReservation.team_id == context.team_id)
            )
            or 0
        )
        if quota.monthly_token_limit is not None and tokens + pending >= quota.monthly_token_limit:
            raise AppError(ErrorCode.QUOTA_EXCEEDED, http_status=429)
        self.session.add(
            UsageReservation(
                reservation_id=reservation_id,
                team_id=context.team_id,
                user_id=context.user_id,
                expires_at=now + timedelta(hours=1),
                created_by=context.user_id,
            )
        )
        await self.session.flush()

    async def record(self, record: CallRecord, reservation_id: str | None) -> None:
        if record.team_id is not None:
            await self.quota(record.team_id, True)
        if reservation_id is not None:
            await self.session.execute(
                delete(UsageReservation).where(UsageReservation.reservation_id == reservation_id)
            )
        insert = (
            pg_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
        )
        statement = insert(UsageDaily).values(
            date=datetime.now(UTC).date(),
            team_id=record.team_id,
            user_id=record.user_id,
            model_id=record.model_id,
            calls=1,
            tokens_in=record.usage.tokens_in,
            tokens_out=record.usage.tokens_out,
            created_by=record.user_id,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["date", "user_id", "model_id"],
                set_={
                    "calls": UsageDaily.calls + 1,
                    "tokens_in": UsageDaily.tokens_in + record.usage.tokens_in,
                    "tokens_out": UsageDaily.tokens_out + record.usage.tokens_out,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    async def trend(
        self,
        actor: CurrentUser,
        by: str,
        since: date,
        until: date,
    ) -> list[tuple[date, str, int, int, int]]:
        group = UsageDaily.team_id if by == "team" else UsageDaily.model_id
        query = (
            select(
                UsageDaily.date,
                group,
                func.sum(UsageDaily.calls),
                func.sum(UsageDaily.tokens_in),
                func.sum(UsageDaily.tokens_out),
            )
            .where(UsageDaily.date >= since, UsageDaily.date <= until)
            .group_by(UsageDaily.date, group)
            .order_by(UsageDaily.date, group)
        )
        if actor.role == Role.LEADER:
            query = query.where(UsageDaily.team_id == actor.team_id)
        elif actor.role == Role.MEMBER:
            query = query.where(UsageDaily.user_id == actor.id)
        rows = await self.session.execute(query)
        return [
            (day, "" if key is None else str(key), int(calls or 0), int(tin or 0), int(tout or 0))
            for day, key, calls, tin, tout in rows
        ]

    async def month_tokens(self, since: date) -> dict[int, int]:
        rows = await self.session.execute(
            select(UsageDaily.team_id, func.sum(UsageDaily.tokens_in + UsageDaily.tokens_out))
            .where(UsageDaily.date >= since, UsageDaily.team_id.is_not(None))
            .group_by(UsageDaily.team_id)
        )
        return {int(team): int(total or 0) for team, total in rows if team is not None}

    async def limited_quotas(self) -> list[TeamQuota]:
        rows = await self.session.scalars(
            select(TeamQuota).where(
                (TeamQuota.monthly_token_limit.is_not(None))
                | (TeamQuota.daily_query_limit.is_not(None))
            )
        )
        return list(rows)

    async def search(
        self, actor: CurrentUser, page: PageParams, since: date | None, until: date | None
    ) -> tuple[list[UsageDaily], int]:
        query = select(UsageDaily)
        if actor.role == Role.LEADER:
            query = query.where(UsageDaily.team_id == actor.team_id)
        elif actor.role == Role.MEMBER:
            query = query.where(UsageDaily.user_id == actor.id)
        if since:
            query = query.where(UsageDaily.date >= since)
        if until:
            query = query.where(UsageDaily.date <= until)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            await self.session.scalars(
                query.order_by(UsageDaily.date.desc(), UsageDaily.id.desc())
                .offset(page.offset)
                .limit(page.page_size)
            )
        )
        return rows, total
