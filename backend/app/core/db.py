from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Concatenate, cast

from sqlalchemy import BigInteger, DateTime, Integer, Select, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession as AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.errors import AppError, ErrorCode


class Base(DeclarativeBase):
    pass


class AuditFields:
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, echo=False, hide_parameters=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()


def transactional[R, **P](
    function: Callable[Concatenate[AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[AsyncSession, P], Awaitable[R]]:
    @wraps(function)
    async def wrapped(session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs) -> R:
        # Nested service calls share their caller's transaction and audit atomicity.
        if session.in_transaction():
            return await function(session, *args, **kwargs)
        async with session.begin():
            return await function(session, *args, **kwargs)

    return wrapped


class BaseRepository[M: Base]:
    def __init__(self, session: AsyncSession, model: type[M]) -> None:
        self.session, self.model = session, model

    def _attribute(self, name: str) -> Any:
        return getattr(self.model, name)

    def _select(self) -> Select[M]:
        query = select(self.model)
        if hasattr(self.model, "deleted_at"):
            query = query.where(self._attribute("deleted_at").is_(None))
        return query

    async def get(self, identifier: int) -> M | None:
        query = self._select().where(self._attribute("id") == identifier)
        return cast(M | None, await self.session.scalar(query))

    async def list(self, offset: int = 0, limit: int = 20) -> list[M]:
        if offset < 0 or limit < 1 or limit > 100:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        rows = await self.session.scalars(
            self._select().order_by(self._attribute("id")).offset(offset).limit(limit)
        )
        return list(rows)

    async def create(self, values: dict[str, Any], actor_id: int) -> M:
        values = {
            key: value
            for key, value in values.items()
            if key not in {"id", "created_at", "updated_at", "created_by", "deleted_at"}
        }
        row = self.model(**values, created_by=actor_id)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, row: M, values: dict[str, Any]) -> M:
        for key, value in values.items():
            if key in {"id", "created_at", "created_by", "deleted_at"}:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            if not hasattr(self.model, key):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            setattr(row, key, value)
        await self.session.flush()
        return row

    async def soft_delete(self, row: M) -> None:
        if not hasattr(row, "deleted_at"):
            raise AppError(ErrorCode.CONFLICT)
        name = "deleted_at"
        setattr(row, name, datetime.now(UTC))
        await self.session.flush()
