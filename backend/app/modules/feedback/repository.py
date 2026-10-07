from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.deps import CurrentUser, Role
from app.core.response import PageParams
from app.modules.feedback.models import ExampleSql, Feedback


class FeedbackRepository(BaseRepository[Feedback]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Feedback)
        self.examples = BaseRepository(session, ExampleSql)

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    async def locked(self, identifier: int) -> Feedback | None:
        return await self.session.scalar(
            self._select().where(Feedback.id == identifier).with_for_update()
        )

    async def search(
        self, actor: CurrentUser, page: PageParams, status: str | None = None
    ) -> tuple[list[Feedback], int]:
        query = self._select().where(Feedback.expires_at > datetime.now(UTC))
        if actor.role == Role.MEMBER:
            query = query.where(Feedback.user_id == actor.id)
        elif actor.role == Role.LEADER:
            query = query.where(Feedback.team_id == actor.team_id)
        if status is not None:
            query = query.where(Feedback.status == status)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            await self.session.scalars(
                query.order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .offset(page.offset)
                .limit(page.page_size)
            )
        )
        return rows, total

    async def example_candidates(self, datasource_id: int) -> list[ExampleSql]:
        return list(
            await self.session.scalars(
                select(ExampleSql)
                .where(ExampleSql.datasource_id == datasource_id, ExampleSql.deleted_at.is_(None))
                .order_by(ExampleSql.id)
            )
        )

    async def locked_example(self, identifier: int) -> ExampleSql | None:
        return await self.session.scalar(
            select(ExampleSql)
            .where(ExampleSql.id == identifier, ExampleSql.deleted_at.is_(None))
            .with_for_update()
        )
