from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.errors import AppError, ErrorCode
from app.core.response import PageParams
from app.modules.chat.models import Conversation, Message, MessageResult
from app.modules.chat.schemas import (
    AnswerMeta,
    ChartSpec,
    Clarification,
    ConversationCreate,
    ConversationView,
    MessageView,
    ResultSnapshot,
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
        self, user_id: int, page: PageParams, query: str = ""
    ) -> tuple[list[ConversationView], int]:
        condition = [Conversation.user_id == user_id, Conversation.deleted_at.is_(None)]
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            condition.append(Conversation.title.ilike(f"%{escaped}%", escape="\\"))
        total = await self.session.scalar(
            select(func.count()).select_from(Conversation).where(*condition)
        )
        rows = await self.session.scalars(
            select(Conversation)
            .where(*condition)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
        return [ConversationView.model_validate(row) for row in rows], total or 0

    async def rename(self, identifier: int, user_id: int, title: str) -> ConversationView:
        await self.get(identifier, user_id)
        row = await self.conversations.get(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.conversations.update(row, {"title": title})
        return ConversationView.model_validate(row)

    async def delete(self, identifier: int, user_id: int) -> None:
        await self.get(identifier, user_id)
        row = await self.conversations.get(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.conversations.soft_delete(row)
        # Kept rows go with the conversation now, not when its messages expire.
        await self.session.execute(
            delete(MessageResult).where(MessageResult.conversation_id == identifier)
        )

    async def save_result(self, identifier: int, message_id: int, snapshot: ResultSnapshot) -> None:
        """Keep an answer's rows; the first save wins (two tabs re-reading an old answer)."""
        await self.session.execute(
            insert(MessageResult)
            .values(message_id=message_id, conversation_id=identifier, **snapshot.model_dump())
            .on_conflict_do_nothing(index_elements=["message_id"])
        )

    async def result(self, message_id: int) -> ResultSnapshot | None:
        row = await self.session.get(MessageResult, message_id)
        return ResultSnapshot.model_validate(row) if row is not None else None

    async def result_sources(
        self, message_ids: list[int]
    ) -> dict[int, tuple[dict[str, list[str]], list[str]]]:
        """Tables, columns and masked outputs behind kept results, without their rows."""
        if not message_ids:
            return {}
        rows = await self.session.execute(
            select(MessageResult.message_id, MessageResult.sources, MessageResult.masked).where(
                MessageResult.message_id.in_(message_ids)
            )
        )
        return {row.message_id: (row.sources, row.masked) for row in rows}

    async def history(self, identifier: int, limit: int = 11) -> list[MessageView]:
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
        chart: ChartSpec | None = None,
        meta: AnswerMeta | None = None,
    ) -> MessageView:
        row = Message(
            conversation_id=identifier,
            user_id=user_id,
            role=role,
            content=content,
            query_run_id=query_run_id,
            query_run_created_at=query_run_created_at,
            normalized_sql=sql,
            chart_spec=chart.model_dump_json() if chart else None,
            answer_meta=meta.model_dump_json() if meta else None,
            created_by=user_id,
            clarification_question=clarification.question if clarification else None,
            clarification_choices=json.dumps(clarification.choices) if clarification else None,
            clarification_answered=False,
            parent_message_id=parent_message_id,
            expires_at=datetime.now(UTC) + timedelta(days=90),
        )
        self.session.add(row)
        # Any new message is activity: the conversation moves to the top of the list.
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == identifier)
            .values(updated_at=datetime.now(UTC))
        )
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

    async def rename_default(self, identifier: int, default: str, title: str) -> bool:
        """Set a generated title only while the conversation still has the default one."""
        renamed = await self.session.execute(
            update(Conversation)
            .where(Conversation.id == identifier, Conversation.title == default)
            .values(title=title)
            .returning(Conversation.id)
        )
        return renamed.scalar_one_or_none() is not None

    async def consume_clarification(
        self, identifier: int, message_id: int, choice: str
    ) -> tuple[str, str | None]:
        """Mark the clarification answered; return the original question and the choice's ref."""
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
        choices = json.loads(row.clarification_choices)
        if choice not in choices:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        refs = (
            AnswerMeta.model_validate_json(row.answer_meta).clarification_refs
            if (row.answer_meta)
            else []
        )
        index = choices.index(choice)
        ref = refs[index] if index < len(refs) else None
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
        return parent.content, ref

    async def asked_before(self, identifier: int, message_id: int) -> str:
        """The user message an assistant message answered; empty when unknown."""
        row = await self.session.scalar(
            select(Message).where(Message.id == message_id, Message.conversation_id == identifier)
        )
        if row is None or row.parent_message_id is None:
            return ""
        parent = await self.session.get(Message, row.parent_message_id)
        return parent.content if parent is not None and parent.role == "user" else ""

    async def assistant_message(self, identifier: int, message_id: int) -> MessageView:
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
        return MessageView.model_validate(row)

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
