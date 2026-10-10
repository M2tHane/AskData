import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.core.audit import (
    AuditLog,
    AuditService,
    DatabaseAuditStore,
    DatabaseQueryLogStore,
    QueryLogWriter,
    QueryRun,
)
from app.core.config import get_settings
from app.core.db import Base, Database
from app.core.errors import AppError, ErrorCode
from app.core.response import PageParams
from app.modules.chat.models import Message
from app.modules.chat.repository import ChatRepository
from app.modules.chat.schemas import (
    ConversationCreate,
    ConversationRename,
    QuestionRequest,
    RerunRequest,
)
from app.modules.chat.service import ChatService

ROOT = Path(__file__).resolve().parents[1]
pytest_plugins = ["tests.test_chat"]


@pytest.fixture
async def chat_database(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, configuration, "head")
    database = Database(url)
    async with database.transaction() as session:
        await session.execute(Base.metadata.tables["team"].insert().values(id=1, name="chat"))
        await session.execute(
            Base.metadata.tables["user"]
            .insert()
            .values(
                id=3,
                username="chat",
                password_hash="fixture",
                role="member",
                team_id=1,
            )
        )
        await session.execute(
            Base.metadata.tables["datasource"]
            .insert()
            .values(
                id=1,
                name="chat",
                type="postgresql",
                host="fake",
                port=5432,
                database="fake",
                username="readonly",
                secret_enc="fixture",
                dialect="postgres",
                owner_team_id=1,
            )
        )
    yield database
    await database.close()
    get_settings.cache_clear()


async def test_real_repository_commits_run_and_audit_even_when_query_is_blocked(
    chat_database, system, member
):
    pipeline = system[0].pipeline
    async with chat_database.session() as session:
        repository = ChatRepository(session)
        pipeline.deps.history = repository.history
        pipeline.deps.querylog = QueryLogWriter(DatabaseQueryLogStore(session))
        service = ChatService(repository, pipeline, AuditService(DatabaseAuditStore(session)))
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        reply = await service.rerun(
            conversation.id, RerunRequest(sql="SELECT secret FROM orders"), actor=member
        )
        assert reply.status == "blocked" and reply.query_run_id
        assert (await service.list(PageParams(), actor=member)).total == 1
        assert (await service.messages(conversation.id, PageParams(), actor=member)).total == 2
        with pytest.raises(AppError) as caught:
            await repository.get(conversation.id, 99)
        assert caught.value.code == ErrorCode.NOT_FOUND
    async with chat_database.session() as session:
        queries = list(await session.scalars(select(QueryRun)))
        audits = list(await session.scalars(select(AuditLog)))
        messages = list(await session.scalars(select(Message).order_by(Message.id)))
        assert len(queries) == 1 and queries[0].payload["status"] == "blocked"
        assert queries[0].payload["conversation_id"] == conversation.id
        assert queries[0].payload["message_id"] == messages[0].id
        assert messages[1].query_run_id == queries[0].id
        assert messages[1].query_run_created_at == queries[0].created_at
        assert "secret" not in (queries[0].payload.get("generated_sql") or "")
        assert queries[0].payload["normalized_sql"] is None
        assert len(audits) == 3


async def test_conversations_order_by_activity_rename_and_search(chat_database, system, member):
    pipeline = system[0].pipeline
    async with chat_database.session() as session:
        repository = ChatRepository(session)
        pipeline.deps.history = repository.history
        pipeline.deps.querylog = QueryLogWriter(DatabaseQueryLogStore(session))
        service = ChatService(repository, pipeline, AuditService(DatabaseAuditStore(session)))
        first = await service.create(ConversationCreate(datasource_id=1), actor=member)
        second = await service.create(ConversationCreate(datasource_id=1), actor=member)

        async def listed(query=""):
            page = await service.list(PageParams(), actor=member, query=query)
            return [item.id for item in page.items]

        assert await listed() == [second.id, first.id]
        # Asking in the older conversation moves it to the top.
        await service.rerun(first.id, RerunRequest(sql="SELECT 1"), actor=member)
        assert await listed() == [first.id, second.id]
        renamed = await service.rename(
            second.id, ConversationRename(title="  季度  复盘 "), actor=member
        )
        assert renamed.title == "季度 复盘"
        assert await listed() == [second.id, first.id]
        assert await listed("复盘") == [second.id]
        # LIKE wildcards are matched literally.
        assert await listed("%") == [] and await listed("_") == []
        with pytest.raises(AppError):
            ConversationRename(title="   ")
        with pytest.raises(AppError) as caught:
            await repository.rename(second.id, 99, "别人的")
        assert caught.value.code == ErrorCode.NOT_FOUND
    async with chat_database.session() as session:
        actions = [row.payload.get("action") for row in await session.scalars(select(AuditLog))]
        assert "chat.rename" in actions


async def test_real_clarification_atomic_consumption_and_soft_delete(chat_database, system, member):
    pipeline = system[0].pipeline
    async with chat_database.session() as session:
        repository = ChatRepository(session)
        pipeline.deps.history = repository.history
        pipeline.deps.querylog = QueryLogWriter(DatabaseQueryLogStore(session))
        service = ChatService(repository, pipeline, AuditService(DatabaseAuditStore(session)))
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        reply = await service.ask(
            conversation.id, QuestionRequest(question="最近订单"), actor=member
        )
        assert reply.status == "clarify"
        page = await service.messages(conversation.id, PageParams(), actor=member)
        pending = next(item for item in page.items if item.id == reply.message_id)
        assert pending.clarification_choices == ["近7天", "近30天"]
        assert pending.clarification_question == reply.clarification.question
        assert not pending.clarification_answered
        continuation = QuestionRequest(
            question="ignored",
            clarification_message_id=reply.message_id,
            clarification_choice="近7天",
        )
        assert (await service.ask(conversation.id, continuation, actor=member)).status == "success"
        with pytest.raises(AppError) as caught:
            await service.ask(conversation.id, continuation, actor=member)
        assert caught.value.code == ErrorCode.CONFLICT
        await service.delete(conversation.id, actor=member)
        assert (await service.list(PageParams(), actor=member)).total == 0
        with pytest.raises(AppError):
            await service.get(conversation.id, actor=member)
    async with chat_database.session() as session:
        assert len(list(await session.scalars(select(QueryRun)))) == 2


async def test_persisted_query_view_keeps_effective_definitions_and_versions(
    chat_database, system, member
):
    from app.modules.chat.pipeline import ContextBuilder, ScopeOnlyIndex
    from app.modules.chat.schemas import ContextItem
    from app.modules.querylog.service import build_service as build_querylog

    class Index(ScopeOnlyIndex):
        async def retrieve(self, user, scope, question, mode):
            bundle = await super().retrieve(user, scope, question, mode)
            bundle.items = [
                ContextItem(
                    kind="metric",
                    ref_id=77,
                    key="orders",
                    name="订单",
                    definition="订单口径",
                    version=3,
                    scope="platform",
                    status="active",
                    tables={"orders": {"id"}},
                )
            ]
            return bundle

    pipeline = system[0].pipeline
    async with chat_database.session() as session:
        repository = ChatRepository(session)
        pipeline.deps.history = repository.history
        pipeline.deps.builder = ContextBuilder(Index())
        pipeline.deps.querylog = QueryLogWriter(DatabaseQueryLogStore(session))
        service = ChatService(repository, pipeline, AuditService(DatabaseAuditStore(session)))
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        reply = await service.ask(conversation.id, QuestionRequest(question="订单"), actor=member)
        query = await build_querylog(session).get(reply.query_run_id, member)
        assert query.context_snapshot["metrics"][0]["ref_id"] == 77
        assert query.context_snapshot["metrics"][0]["version"] == 3
        assert query.context_snapshot["metrics"][0]["scope"] == "platform"
        assert query.model_id == "fixed" and query.tokens_in == 20
        assert query.prompt_versions == {"sql_generation": "v9", "answer": "v3"}
        assert "rows" not in query.context_snapshot


async def test_chat_retention_removes_expired_messages_without_deleting_live_children(
    chat_database, member
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.modules.chat.service import build_retention_store

    now = datetime.now(UTC)
    async with chat_database.transaction() as session:
        await session.execute(text("PRAGMA foreign_keys = ON"))
        repository = ChatRepository(session)
        conversation = await repository.create(
            member.id, member.team_id, ConversationCreate(datasource_id=1)
        )
        parent = await repository.add_message(conversation.id, member.id, "user", "old question")
        child = await repository.add_message(
            conversation.id, member.id, "assistant", "live answer", parent_message_id=parent.id
        )
        parent_row = await session.get(Message, parent.id)
        parent_row.expires_at = now - timedelta(days=1)
        assert await build_retention_store(session).purge_before(now) == 1
        child_row = await session.get(Message, child.id)
        await session.refresh(child_row)
        assert child_row.content == "live answer" and child_row.parent_message_id is None


def quota_chat_service(database, session, pipeline):
    from app.modules.usage.repository import UsageRepository
    from app.modules.usage.service import UsageService

    repository = ChatRepository(session)
    audit = AuditService(DatabaseAuditStore(session))
    pipeline.deps.history = repository.history
    pipeline.deps.querylog = QueryLogWriter(DatabaseQueryLogStore(session))
    pipeline.deps.llm.recorder = UsageService(UsageRepository(session, database.sessions), audit)
    return ChatService(repository, pipeline, audit)


async def test_daily_question_quota_survives_reconnect_and_one_round_clarification(
    chat_database, system, member
):
    from app.modules.usage.models import TeamQuota, UsageDaily, UsageQuery

    async with chat_database.transaction() as session:
        session.add(TeamQuota(team_id=1, daily_query_limit=1))
    pipeline = system[0].pipeline
    async with chat_database.session() as session:
        service = quota_chat_service(chat_database, session, pipeline)
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        first = await service.ask(
            conversation.id, QuestionRequest(question="最近订单"), actor=member
        )
        assert first.status == "clarify"
    # Rebuild every service after reconnect; no request-local flag can bypass/double bill quota.
    async with chat_database.session() as session:
        service = quota_chat_service(chat_database, session, pipeline)
        continued = await service.ask(
            conversation.id,
            QuestionRequest(
                question="ignored",
                clarification_message_id=first.message_id,
                clarification_choice="近7天",
            ),
            actor=member,
        )
        assert continued.status == "success"
        queries = list(await session.scalars(select(QueryRun).order_by(QueryRun.id)))
        assert queries[0].payload["query_id"] == queries[1].payload["query_id"]
        assert (await session.scalar(select(TeamQuota))).daily_calls == 1
        assert len(list(await session.scalars(select(UsageQuery)))) == 1
        assert sum(row.calls for row in await session.scalars(select(UsageDaily))) > 1
        calls, executions = len(system[1].calls_by_prompt), len(system[2].executed)
        denied = await service.ask(
            conversation.id, QuestionRequest(question="同样的订单问题"), actor=member
        )
        assert denied.code == ErrorCode.QUOTA_EXCEEDED and denied.status == "failed"
        assert denied.explanation and not denied.rows
        assert len(system[1].calls_by_prompt) == calls and len(system[2].executed) == executions
        latest = await session.scalar(select(QueryRun).order_by(QueryRun.id.desc()).limit(1))
        assert latest.payload["error"] == ErrorCode.QUOTA_EXCEEDED.value
        assert latest.payload["query_id"] != queries[0].payload["query_id"]


async def test_zero_question_quota_denies_manual_sql_and_sse_before_any_model_or_execution(
    chat_database, system, member
):
    from app.core.stream import EventType
    from app.modules.usage.models import TeamQuota, UsageDaily, UsageQuery

    async with chat_database.transaction() as session:
        session.add(TeamQuota(team_id=1, daily_query_limit=0))
    async with chat_database.session() as session:
        service = quota_chat_service(chat_database, session, system[0].pipeline)
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        rerun = await service.rerun(
            conversation.id, RerunRequest(sql="SELECT id FROM orders"), actor=member
        )
        assert rerun.code == ErrorCode.QUOTA_EXCEEDED
        ctx = await service.prepare(conversation.id, QuestionRequest(question="订单"), actor=member)
        events = [event async for event in service.stream(ctx)]
        kinds = [event.event for event in events]
        assert kinds[-1] == EventType.DONE and kinds.count(EventType.ERROR) == 1
        error = events[kinds.index(EventType.ERROR)]
        assert error.data["code"] == "QUOTA_EXCEEDED" and error.data["message"]
        assert not system[1].calls_by_prompt and not system[2].executed
        assert not list(await session.scalars(select(UsageDaily)))
        assert not list(await session.scalars(select(UsageQuery)))


async def test_blocked_question_admission_survives_and_next_question_is_denied(
    chat_database, system, member
):
    from app.modules.usage.models import TeamQuota, UsageQuery

    async with chat_database.transaction() as session:
        session.add(TeamQuota(team_id=1, daily_query_limit=1))
    async with chat_database.session() as session:
        service = quota_chat_service(chat_database, session, system[0].pipeline)
        conversation = await service.create(ConversationCreate(datasource_id=1), actor=member)
        blocked = await service.rerun(
            conversation.id, RerunRequest(sql="DELETE FROM orders"), actor=member
        )
        assert blocked.status == "blocked"
    async with chat_database.session() as session:
        service = quota_chat_service(chat_database, session, system[0].pipeline)
        denied = await service.ask(conversation.id, QuestionRequest(question="订单"), actor=member)
        assert denied.code == ErrorCode.QUOTA_EXCEEDED and not system[2].executed
        assert len(list(await session.scalars(select(UsageQuery)))) == 1
