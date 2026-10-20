from sqlalchemy import func, select, update

from app.core.db import AsyncSession, BaseRepository
from app.modules.modelcfg.models import ModelConfiguration, TeamModelPreference


class ModelRepository(BaseRepository[ModelConfiguration]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ModelConfiguration)

    async def all(self) -> list[ModelConfiguration]:
        return list(await self.session.scalars(self._select().order_by(ModelConfiguration.id)))

    async def enabled_list(self, offset: int, limit: int) -> list[ModelConfiguration]:
        return list(
            await self.session.scalars(
                self._select()
                .where(ModelConfiguration.enabled.is_(True))
                .order_by(ModelConfiguration.id)
                .offset(offset)
                .limit(limit)
            )
        )

    async def count(self, enabled_only: bool = False) -> int:
        query = (
            select(func.count())
            .select_from(ModelConfiguration)
            .where(ModelConfiguration.deleted_at.is_(None))
        )
        if enabled_only:
            query = query.where(ModelConfiguration.enabled.is_(True))
        return int(await self.session.scalar(query) or 0)

    async def preference(self, team_id: int | None, kind: str) -> int | None:
        return await self.session.scalar(
            select(TeamModelPreference.model_id).where(
                TeamModelPreference.team_id == team_id, TeamModelPreference.type == kind
            )
        )

    async def set_preference(self, team_id: int, kind: str, model_id: int, actor_id: int) -> None:
        row = await self.session.scalar(
            select(TeamModelPreference).where(
                TeamModelPreference.team_id == team_id, TeamModelPreference.type == kind
            )
        )
        if row is None:
            row = TeamModelPreference(
                team_id=team_id, type=kind, model_id=model_id, created_by=actor_id
            )
            self.session.add(row)
        else:
            row.model_id = model_id
        await self.session.flush()

    async def used(self, model_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(TeamModelPreference.id)
                .where(TeamModelPreference.model_id == model_id)
                .limit(1)
            )
        )

    async def clear_default(self, kind: str) -> None:
        await self.session.execute(
            update(ModelConfiguration)
            .where(ModelConfiguration.type == kind)
            .values(is_default=False)
        )
