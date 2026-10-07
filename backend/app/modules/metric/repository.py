from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.modules.metric.models import Metric


class MetricRepository(BaseRepository[Metric]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Metric)

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        # Own the request transaction, including any reads and audit writes.
        async with self.session.begin_nested():
            yield

    async def candidates(self, datasource_id: int) -> list[Metric]:
        return list(
            await self.session.scalars(
                self._select().where(Metric.datasource_id == datasource_id).order_by(Metric.id)
            )
        )

    async def locked(self, identifier: int) -> Metric | None:
        return await self.session.scalar(
            self._select().where(Metric.id == identifier).with_for_update()
        )
