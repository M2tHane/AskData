from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy import Table, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import LogEvent, logger


class RetentionStore(Protocol):
    async def purge(self, before: datetime) -> dict[str, int]:
        """Purge expired logs, feedback and upload history; preserve promoted eval cases."""
        ...


class RetentionResult(BaseModel):
    cutoff: datetime
    removed: dict[str, int] = Field(default_factory=dict)


class RetentionService:
    def __init__(self, store: RetentionStore) -> None:
        self.store = store

    async def run(self, now: datetime | None = None) -> RetentionResult:
        cutoff = now or datetime.now(UTC)
        removed = await self.store.purge(cutoff)
        logger.info(LogEvent.RETENTION, count=sum(removed.values()))
        return RetentionResult(cutoff=cutoff, removed=removed)


class DatabaseRetentionStore:
    """Register only owned tables; later modules plug into CompositeRetentionStore.

    The caller supplies one transaction. Foreign-key cascades remove dependent snapshots.
    """

    def __init__(self, session: AsyncSession, tables: list[Table]) -> None:
        self.session, self.tables = session, tables

    async def purge(self, before: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in self.tables:
            result = await self.session.execute(
                delete(table)
                .where(
                    table.c.expires_at <= before,
                )
                .returning(table.c.id)
            )
            counts[table.name] = len(result.fetchall())
        return counts


class CompositeRetentionStore:
    def __init__(self, stores: list[RetentionStore]) -> None:
        self.stores = stores

    async def purge(self, before: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        for store in self.stores:
            for name, count in (await store.purge(before)).items():
                counts[name] = counts.get(name, 0) + count
        return counts
