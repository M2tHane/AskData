from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.errors import AppError, ErrorCode
from app.core.response import PageParams
from app.modules.chat.models import Conversation, Message
from app.modules.chat.schemas import (
    Clarification,
    ConversationCreate,
    ConversationView,
    MessageView,
)


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = BaseRepository(session, Conversation)

    async def purge_before(self, now: datetime) -> int:
        deleted = await self.session.scalars(
            delete(Message).where(Message.expires_at <= now).returning(Message.id)
        )
        return len(list(deleted))

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def create(
        self, user_id: int, team_id: int | None, request: ConversationCreate
    ) -> ConversationView:
        row = await self.conversations.create(
            {**request.model_dump(), "user_id": user_id, "team_id": team_id}, user_id
        )
        return ConversationView.model_validate(row)

    async def get(self, identifier: int, user_id: int) -> ConversationView:
        row = await self.session.scalar(
            select(Conversation).where(
                Conversation.id == identifier,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
        )
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return ConversationView.model_validate(row)

    async def list_conversations(
        self, user_id: int, page: PageParams
    ) -> tuple[list[ConversationView], int]:
        condition = (Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
        total = await self.session.scalar(
            select(func.count()).select_from(Conversation).where(*condition)
        )
        rows = await self.session.scalars(
            select(Conversation)
            .where(*condition)
            .order_by(Conversation.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
        return [ConversationView.model_validate(row) for row in rows], total or 0

    async def delete(self, identifier: int, user_id: int) -> None:
        await self.get(identifier, user_id)
        row = await self.conversations.get(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.conversations.soft_delete(row)

    async def history(self, identifier: int, limit: int = 12) -> list[MessageView]:
        rows = await self.session.scalars(
            select(Message)
            .where(
                Message.conversation_id == identifier,
                Message.expires_at > datetime.now(UTC),
            )
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return [MessageView.model_validate(row) for row in reversed(list(rows))]

    async def messages(self, identifier: int, page: PageParams) -> tuple[list[MessageView], int]:
        condition = (Message.conversation_id == identifier, Message.expires_at > datetime.now(UTC))
        total = await self.session.scalar(
            select(func.count()).select_from(Message).where(*condition)
        )
        rows = await self.session.scalars(
            select(Message)
            .where(*condition)
            .order_by(Message.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
        return [MessageView.model_validate(row) for row in rows], total or 0

    async def add_message(
        self,
        identifier: int,
        user_id: int,
        role: str,
        content: str,
        query_run_id: int | None = None,
        sql: str | None = None,
        clarification: Clarification | None = None,
        parent_message_id: int | None = None,
        query_run_created_at: datetime | None = None,
    ) -> MessageView:
        row = Message(
            conversation_id=identifier,
            user_id=user_id,
            role=role,
            content=content,
            query_run_id=query_run_id,
            query_run_created_at=query_run_created_at,
            normalized_sql=sql,
            created_by=user_id,
            clarification_question=clarification.question if clarification else None,
            clarification_choices=json.dumps(clarification.choices) if clarification else None,
            clarification_answered=False,
            parent_message_id=parent_message_id,
            expires_at=datetime.now(UTC) + timedelta(days=90),
        )
        self.session.add(row)
        await self.session.flush()
        return MessageView.model_validate(row)

    async def clarification_query_id(self, identifier: int, message_id: int) -> str:
        row = await self.session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == identifier,
                Message.role == "assistant",
                Message.expires_at > datetime.now(UTC),
            )
        )
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if row.parent_message_id is None:
            raise AppError(ErrorCode.CONFLICT)
        return f"chat:{identifier}:{row.parent_message_id}"

    async def consume_clarification(self, identifier: int, message_id: int, choice: str) -> str:
        row = await self.session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == identifier,
                Message.role == "assistant",
                Message.expires_at > datetime.now(UTC),
            )
        )
        if row is None or not row.clarification_choices:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if choice not in json.loads(row.clarification_choices):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        consumed = await self.session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.clarification_answered.is_(False),
            )
            .values(clarification_answered=True)
            .returning(Message.id)
        )
        if consumed.scalar_one_or_none() is None:
            raise AppError(ErrorCode.CONFLICT)
        parent = await self.session.get(Message, row.parent_message_id)
        if parent is None:
            raise AppError(ErrorCode.CONFLICT)
        return parent.content

    async def execution_sql(self, identifier: int, message_id: int) -> str:
        row = await self.session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == identifier,
                Message.expires_at > datetime.now(UTC),
            )
        )
        if row is None or not row.normalized_sql:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row.normalized_sql
