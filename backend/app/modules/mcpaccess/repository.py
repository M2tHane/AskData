from datetime import date, datetime
from typing import Any

from sqlalchemy import case, delete, or_, select, update

from app.core.db import AsyncSession, BaseRepository
from app.modules.mcpaccess.models import McpApiKey, McpClient, McpConversation, McpItemOverride


class McpRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.clients = BaseRepository(session, McpClient)

    async def list_clients(self) -> list[McpClient]:
        return list(await self.session.scalars(self.clients._select().order_by(McpClient.id)))

    async def by_public_id(self, public_id: str) -> McpClient | None:
        return await self.session.scalar(
            self.clients._select().where(McpClient.public_id == public_id)
        )

    async def keys(self, client_id: int) -> list[McpApiKey]:
        return list(
            await self.session.scalars(
                select(McpApiKey).where(McpApiKey.client_id == client_id).order_by(McpApiKey.id)
            )
        )

    async def key_by_hash(self, key_hash: str) -> McpApiKey | None:
        return await self.session.scalar(select(McpApiKey).where(McpApiKey.key_hash == key_hash))

    async def add_key(self, client_id: int, prefix: str, key_hash: str, actor_id: int) -> McpApiKey:
        row = McpApiKey(client_id=client_id, prefix=prefix, key_hash=key_hash, created_by=actor_id)
        self.session.add(row)
        await self.session.flush()
        return row

    async def key(self, client_id: int, key_id: int) -> McpApiKey | None:
        return await self.session.scalar(
            select(McpApiKey).where(McpApiKey.client_id == client_id, McpApiKey.id == key_id)
        )

    async def revoke_keys(self, client_id: int, now: datetime) -> None:
        await self.session.execute(
            update(McpApiKey)
            .where(McpApiKey.client_id == client_id, McpApiKey.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def admit(self, client_id: int, today: date) -> bool:
        """Count one call against today's limit, atomically; False when it is used up."""
        result = await self.session.execute(
            update(McpClient)
            .where(
                McpClient.id == client_id,
                or_(
                    McpClient.daily_date.is_(None),
                    McpClient.daily_date != today,
                    McpClient.daily_calls < McpClient.daily_call_limit,
                ),
            )
            .values(
                # A new day starts the count again.
                daily_calls=case(
                    (McpClient.daily_date == today, McpClient.daily_calls + 1), else_=1
                ),
                daily_date=today,
            )
            .returning(McpClient.id)
        )
        return result.first() is not None

    async def overrides(self, client_id: int, kind: str | None = None) -> list[McpItemOverride]:
        query = select(McpItemOverride).where(McpItemOverride.client_id == client_id)
        if kind is not None:
            query = query.where(McpItemOverride.kind == kind)
        return list(await self.session.scalars(query.order_by(McpItemOverride.id)))

    async def set_override(
        self,
        client_id: int,
        kind: str,
        item_id: int,
        action: str,
        payload: dict[str, Any],
        actor_id: int,
    ) -> McpItemOverride:
        row = await self.session.scalar(
            select(McpItemOverride).where(
                McpItemOverride.client_id == client_id,
                McpItemOverride.kind == kind,
                McpItemOverride.item_id == item_id,
            )
        )
        if row is None:
            row = McpItemOverride(
                client_id=client_id, kind=kind, item_id=item_id, created_by=actor_id
            )
            self.session.add(row)
        row.action, row.payload = action, payload
        await self.session.flush()
        return row

    async def drop_overrides(
        self, client_id: int, kind: str | None = None, item_ids: list[int] | None = None
    ) -> int:
        query = delete(McpItemOverride).where(McpItemOverride.client_id == client_id)
        if kind is not None:
            query = query.where(McpItemOverride.kind == kind)
        if item_ids is not None:
            query = query.where(McpItemOverride.item_id.in_(item_ids))
        result = await self.session.execute(query)
        return int(getattr(result, "rowcount", 0) or 0)

    async def conversation(self, client_id: int, public_id: str) -> McpConversation | None:
        return await self.session.scalar(
            select(McpConversation).where(
                McpConversation.client_id == client_id, McpConversation.public_id == public_id
            )
        )

    async def add_conversation(
        self,
        client_id: int,
        public_id: str,
        conversation_id: int,
        datasource_id: int,
        now: datetime,
    ) -> McpConversation:
        row = McpConversation(
            client_id=client_id,
            public_id=public_id,
            conversation_id=conversation_id,
            datasource_id=datasource_id,
            last_used_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def drop_conversations(self, client_id: int) -> list[int]:
        """Forget the connection's public ids; returns the conversations they pointed to."""
        rows = list(
            await self.session.scalars(
                select(McpConversation.conversation_id).where(
                    McpConversation.client_id == client_id
                )
            )
        )
        await self.session.execute(
            delete(McpConversation).where(McpConversation.client_id == client_id)
        )
        return rows

    async def commit(self) -> None:
        await self.session.commit()
