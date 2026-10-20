from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.deps import CurrentUser, Role
from app.modules.promotion.models import PromotionRequest


class PromotionRepository(BaseRepository[PromotionRequest]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PromotionRequest)

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    async def locked(self, identifier: int) -> PromotionRequest | None:
        return await self.session.scalar(
            self._select().where(PromotionRequest.id == identifier).with_for_update()
        )

    async def visible(self, actor: CurrentUser) -> list[PromotionRequest]:
        condition = (
            PromotionRequest.team_id == actor.team_id
            if actor.role == Role.LEADER
            else PromotionRequest.requested_by == actor.id
        )
        return list(
            await self.session.scalars(
                self._select().where(condition).order_by(PromotionRequest.created_at.desc())
            )
        )
