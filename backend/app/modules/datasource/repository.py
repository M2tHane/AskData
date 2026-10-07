from sqlalchemy import func, select

from app.core.db import AsyncSession, BaseRepository
from app.modules.datasource.models import DatasourceRecord


class DatasourceRepository(BaseRepository[DatasourceRecord]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DatasourceRecord)

    async def lock(self, datasource_id: int) -> None:
        await self.session.scalar(
            self._select().where(DatasourceRecord.id == datasource_id).with_for_update()
        )

    async def locked(self, datasource_id: int) -> DatasourceRecord | None:
        return await self.session.scalar(
            self._select()
            .where(DatasourceRecord.id == datasource_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def all_active(self) -> list[DatasourceRecord]:
        return list(
            await self.session.scalars(
                self._select()
                .where(DatasourceRecord.status == "active")
                .order_by(DatasourceRecord.id)
            )
        )

    async def active_ids(self) -> list[int]:
        return list(
            await self.session.scalars(
                select(DatasourceRecord.id).where(
                    DatasourceRecord.status == "active", DatasourceRecord.deleted_at.is_(None)
                )
            )
        )

    async def count(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(DatasourceRecord)
                .where(DatasourceRecord.deleted_at.is_(None))
            )
            or 0
        )
