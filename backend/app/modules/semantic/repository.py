from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.modules.semantic.models import SemanticTerm


class SemanticTermRepository(BaseRepository[SemanticTerm]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SemanticTerm)

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        # Own the request transaction, including any reads and audit writes.
        async with self.session.begin_nested():
            yield

    async def candidates(self, datasource_id: int) -> list[SemanticTerm]:
        return list(
            await self.session.scalars(
                self._select()
                .where(SemanticTerm.datasource_id == datasource_id)
                .order_by(SemanticTerm.id)
            )
        )

    async def locked(self, identifier: int) -> SemanticTerm | None:
        return await self.session.scalar(
            self._select().where(SemanticTerm.id == identifier).with_for_update()
        )
