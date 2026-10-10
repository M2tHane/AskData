from sqlalchemy import func, select, update

from app.core.db import AsyncSession, BaseRepository
from app.modules.promptcfg.models import PromptRevision


class PromptRepository(BaseRepository[PromptRevision]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PromptRevision)

    async def revisions(self, kind: str, key: str) -> list[PromptRevision]:
        rows = await self.session.scalars(
            select(PromptRevision)
            .where(PromptRevision.kind == kind, PromptRevision.key == key)
            .order_by(PromptRevision.revision.desc())
        )
        return list(rows)

    async def revision(self, kind: str, key: str, identifier: int) -> PromptRevision | None:
        return await self.session.scalar(
            select(PromptRevision).where(
                PromptRevision.id == identifier,
                PromptRevision.kind == kind,
                PromptRevision.key == key,
            )
        )

    async def next_revision(self, kind: str, key: str) -> int:
        top = await self.session.scalar(
            select(func.max(PromptRevision.revision)).where(
                PromptRevision.kind == kind, PromptRevision.key == key
            )
        )
        return int(top or 0) + 1

    async def deactivate(self, kind: str, key: str) -> None:
        await self.session.execute(
            update(PromptRevision)
            .where(PromptRevision.kind == kind, PromptRevision.key == key)
            .values(is_active=False)
        )

    async def active(self) -> list[PromptRevision]:
        rows = await self.session.scalars(
            select(PromptRevision).where(PromptRevision.is_active.is_(True))
        )
        return list(rows)
