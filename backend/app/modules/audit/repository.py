from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.audit.schemas import AuditFilter


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, filters: AuditFilter) -> tuple[list[AuditLog], int]:
        query = select(AuditLog).where(AuditLog.expires_at > datetime.now(UTC))
        if filters.actor_id is not None:
            query = query.where(AuditLog.payload["actor_id"].as_integer() == filters.actor_id)
        for key in ("action", "target_type"):
            if (value := getattr(filters, key)) is not None:
                query = query.where(AuditLog.payload[key].as_string() == value)
        if filters.since:
            query = query.where(AuditLog.created_at >= filters.since)
        if filters.until:
            query = query.where(AuditLog.created_at < filters.until)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            await self.session.scalars(
                query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(filters.offset)
                .limit(filters.page_size)
            )
        )
        return rows, total
