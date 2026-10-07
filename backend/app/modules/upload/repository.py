from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.errors import AppError, ErrorCode
from app.modules.upload.models import UploadJob


class UploadRepository(BaseRepository[UploadJob]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UploadJob)

    @asynccontextmanager
    async def transaction(self) -> Any:
        async with self.session.begin():
            yield

    async def required(self, identifier: int, lock: bool = False) -> UploadJob:
        query = select(UploadJob).where(UploadJob.id == identifier)
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row

    async def allocate(self, values: dict[str, Any], actor_id: int) -> UploadJob:
        # Serializes allocation without accessing datasource's owned table.
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            await self.session.execute(select(func.pg_advisory_xact_lock(values["datasource_id"])))
        version = await self.session.scalar(
            select(func.max(UploadJob.version)).where(
                UploadJob.datasource_id == values["datasource_id"]
            )
        )
        return await self.create({**values, "version": (version or 0) + 1}, actor_id)

    async def history(self, datasource_id: int, offset: int, limit: int) -> list[UploadJob]:
        return list(
            await self.session.scalars(
                select(UploadJob)
                .where(UploadJob.datasource_id == datasource_id)
                .order_by(UploadJob.version.desc())
                .offset(offset)
                .limit(limit)
            )
        )
