from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.errors import AppError, ErrorCode
from app.modules.upload.models import UploadJob
from app.modules.upload.schemas import UploadHistoryRecord


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

    @asynccontextmanager
    async def retention_transaction(self) -> AsyncIterator[None]:
        # Match core retention: join the composition root's transaction when provided.
        async with (
            self.session.begin_nested() if self.session.in_transaction() else self.session.begin()
        ):
            yield

    async def retention_sources(self, expired_at: datetime) -> list[int]:
        return list(
            await self.session.scalars(
                select(UploadJob.datasource_id)
                .where(UploadJob.status == "success", UploadJob.updated_at <= expired_at)
                .distinct()
                .order_by(UploadJob.datasource_id)
            )
        )

    async def locked_history(self, datasource_id: int) -> list[UploadHistoryRecord]:
        # Allocation uses this same advisory lock; version numbers cannot be reused
        # while expired rows are removed. All existing source jobs are locked too.
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            await self.session.execute(select(func.pg_advisory_xact_lock(datasource_id)))
        rows = await self.session.scalars(
            select(UploadJob)
            .where(UploadJob.datasource_id == datasource_id)
            .order_by(UploadJob.version)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return [UploadHistoryRecord.model_validate(row) for row in rows]

    async def other_file_references(self, key: str, identifier: int) -> bool:
        return (
            await self.session.scalar(
                select(UploadJob.id)
                .where(UploadJob.file_key == key, UploadJob.id != identifier)
                .limit(1)
            )
        ) is not None

    async def delete_history(self, identifier: int) -> None:
        result = await self.session.execute(
            delete(UploadJob).where(UploadJob.id == identifier).returning(UploadJob.id)
        )
        if result.scalar_one_or_none() is None:
            raise AppError(ErrorCode.CONFLICT)

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
