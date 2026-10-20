from datetime import datetime

from sqlalchemy import update

from app.core.db import AsyncSession
from app.core.security import RefreshRecord
from app.modules.auth.models import RefreshToken


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, record: RefreshRecord) -> None:
        self.session.add(RefreshToken(**record.model_dump(), created_by=record.user_id))
        await self.session.flush()

    async def consume(self, token_hash: str, now: datetime) -> RefreshRecord | None:
        row = (
            await self.session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.token_hash == token_hash,
                    RefreshToken.revoked.is_(False),
                    RefreshToken.expires_at > now,
                )
                .values(revoked=True)
                .returning(RefreshToken)
            )
        ).scalar_one_or_none()
        return (
            RefreshRecord(
                user_id=row.user_id,
                token_hash=row.token_hash,
                expires_at=row.expires_at,
                revoked=True,
            )
            if row
            else None
        )
