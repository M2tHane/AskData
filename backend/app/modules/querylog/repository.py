from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import QueryLogRecord, QueryRun
from app.core.deps import CurrentUser, Role
from app.core.errors import ErrorCode
from app.modules.querylog.schemas import QueryFilter


class QueryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.last_id: int | None = None

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    async def append(self, record: QueryLogRecord, metadata: dict[str, Any] | None = None) -> int:
        row = QueryRun(
            payload={**record.model_dump(mode="json"), **(metadata or {})},
            created_by=record.user_id,
            expires_at=record.expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        self.last_id = row.id
        return row.id

    def visible(self, actor: CurrentUser) -> Any:
        query = select(QueryRun).where(QueryRun.expires_at > datetime.now(UTC))
        if actor.role == Role.MEMBER:
            query = query.where(QueryRun.payload["user_id"].as_integer() == actor.id)
        elif actor.role == Role.LEADER:
            query = query.where(QueryRun.payload["team_id"].as_integer() == actor.team_id)
        return query

    async def get(self, identifier: int, actor: CurrentUser) -> QueryRun | None:
        return cast(
            QueryRun | None,
            await self.session.scalar(self.visible(actor).where(QueryRun.id == identifier)),
        )

    async def search(self, actor: CurrentUser, filters: QueryFilter) -> tuple[list[QueryRun], int]:
        query = self.visible(actor)
        for key in ("user_id", "datasource_id"):
            if (value := getattr(filters, key)) is not None:
                query = query.where(QueryRun.payload[key].as_integer() == value)
        cancelled = QueryRun.payload["error"].as_string() == ErrorCode.QUERY_CANCELLED.value
        if filters.status == "cancelled":
            query = query.where(QueryRun.payload["status"].as_string() == "failed", cancelled)
        elif filters.status is not None:
            query = query.where(QueryRun.payload["status"].as_string() == filters.status)
            if filters.status == "failed":
                query = query.where(
                    or_(QueryRun.payload["error"].as_string().is_(None), ~cancelled)
                )
        if filters.blocked_code is not None:
            query = query.where(
                QueryRun.payload["blocked_code"].as_string() == filters.blocked_code
            )
        if filters.has_feedback is not None:
            query = query.where(
                func.coalesce(QueryRun.payload["has_feedback"].as_boolean(), False)
                == filters.has_feedback
            )
        if filters.since:
            query = query.where(QueryRun.created_at >= filters.since)
        if filters.until:
            query = query.where(QueryRun.created_at < filters.until)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            await self.session.scalars(
                query.order_by(QueryRun.created_at.desc(), QueryRun.id.desc())
                .offset(filters.offset)
                .limit(filters.page_size)
            )
        )
        return rows, total

    async def mark_feedback(self, identifier: int) -> None:
        row = await self.session.scalar(
            select(QueryRun).where(QueryRun.id == identifier).with_for_update()
        )
        if row is not None:
            row.payload = {**row.payload, "has_feedback": True}
            await self.session.flush()
