import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.core.audit import (
    AuditLog,
    AuditRecord,
    AuditService,
    DatabaseAuditStore,
    DatabaseQueryLogStore,
    QueryLogWriter,
    QueryRun,
    audited,
)
from app.core.db import BaseRepository, Database, transactional
from app.core.errors import AppError, ErrorCode
from app.core.retention import CompositeRetentionStore, DatabaseRetentionStore, RetentionService
from app.core.sql.executor import QueryResult
from app.core.tasks import DatabaseTaskStore, TaskManager, TaskStatus
from tests.fakes import MemoryQueue, MemoryTaskStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def database(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'metadata.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "0001_core")
    db = Database(url)
    yield db
    await db.close()
    get_settings.cache_clear()


async def test_repository_crud_transaction_and_audit_atomicity(database, member):
    async with database.transaction() as session:
        repository = BaseRepository(session, AuditLog)
        row = await repository.create(
            {"payload": {"x": 1}, "expires_at": datetime.now(UTC)}, member.id
        )
        assert row.created_by == member.id
        assert await repository.get(row.id) is row
        assert await repository.list() == [row]
        await repository.update(row, {"payload": {"x": 2}})
        with pytest.raises(AppError):
            await repository.update(row, {"created_by": 99})
    async with database.session() as session:
        assert (await BaseRepository(session, AuditLog).list())[0].payload == {"x": 2}

    @transactional
    async def failing(session):
        await DatabaseAuditStore(session).append(
            AuditRecord(
                actor_id=member.id,
                action="test",
                target_type="test",
                target_id="1",
            )
        )
        raise AppError(ErrorCode.CONFLICT)

    async with database.session() as session:
        with pytest.raises(AppError):
            await failing(session)
    async with database.session() as session:
        assert len(await BaseRepository(session, AuditLog).list()) == 1


async def test_audit_redaction_and_query_preview_retention(database, member, guard, scope):
    sql = guard.check("SELECT phone FROM orders", "postgres", scope)
    result = QueryResult(
        columns=["phone"],
        rows=[{"phone": "13812345678"} for _ in range(20)],
        row_count=20,
        truncated=False,
        duration_ms=1,
    )
    async with database.transaction() as session:
        await AuditService(DatabaseAuditStore(session)).record(
            member,
            "change",
            ("config", "1"),
            {"password": "secret", "name": "safe"},
        )
        await QueryLogWriter(DatabaseQueryLogStore(session)).write(
            member, 1, "success", sql, result
        )
    async with database.transaction() as session:
        audits = list(await session.scalars(select(AuditLog)))
        queries = list(await session.scalars(select(QueryRun)))
        assert audits[0].payload["diff"]["password"] == "[REDACTED]"
        assert len(queries[0].payload["result_preview"]) == 5
        assert queries[0].payload["result_preview"][0]["phone"] == "138****5678"
        # Advance the retention cutoff without mutating server clocks.
        stats = await RetentionService(
            DatabaseRetentionStore(
                session,
                [AuditLog.__table__, QueryRun.__table__],
            )
        ).run(datetime.now(UTC) + timedelta(days=91))
        assert stats.removed == {"audit_log": 1, "query_run": 1}
    async with database.session() as session:
        assert not list(await session.scalars(select(QueryRun)))


async def test_decorator_requires_actor_and_audits_success(member):
    class Store:
        def __init__(self):
            self.records = []

        async def append(self, record):
            self.records.append(record)

    store = Store()

    class Service:
        audit = AuditService(store)

        @audited("create", "item")
        async def create(self, *, actor):
            return actor

    assert await Service().create(actor=member) == member
    assert store.records[0].actor_id == member.id
    with pytest.raises(AppError):
        await Service().create()


@pytest.mark.parametrize("failed", [False, True])
async def test_task_durable_lifecycle_duplicate_delivery_and_progress(database, failed):
    store, queue = DatabaseTaskStore(database.sessions), MemoryQueue()
    calls = []

    async def handler(ctx):
        calls.append(ctx.task.id)
        await ctx.progress(50)
        await ctx.progress(20)
        assert (await store.get(ctx.task.id)).progress == 50
        if failed:
            raise RuntimeError("provider secret")

    manager = TaskManager(store, queue, {"test": handler})
    task = await manager.enqueue("test", {"file_key": "safe"})
    assert (await store.get(task.id)).status == TaskStatus.QUEUED
    await manager.run(task.id)
    await manager.run(task.id)
    assert calls == [task.id]
    finished = await store.get(task.id)
    assert finished.status == (TaskStatus.FAILED if failed else TaskStatus.SUCCESS)
    assert finished.error == (ErrorCode.TASK_FAILED if failed else None)


async def test_queue_failure_and_task_cancellation():
    store, queue = MemoryTaskStore(), MemoryQueue()
    started = asyncio.Event()

    async def handler(ctx):
        started.set()
        await asyncio.Event().wait()

    manager = TaskManager(store, queue, {"test": handler})
    queue.fail = True
    with pytest.raises(AppError):
        await manager.enqueue("test", {})
    assert next(iter(store.records.values())).status == TaskStatus.FAILED
    queue.fail = False
    task = await manager.enqueue("test", {})
    running = asyncio.create_task(manager.run(task.id))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert store.records[task.id].status == TaskStatus.FAILED


async def test_composite_retention():
    class Store:
        async def purge(self, before):
            return {"history": 2}

    result = await CompositeRetentionStore([Store(), Store()]).purge(datetime.now(UTC))
    assert result == {"history": 4}
