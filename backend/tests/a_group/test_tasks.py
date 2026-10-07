from typing import Literal

import pytest
from pydantic import SecretStr

from app.core.errors import AppError
from app.core.tasks import TaskManager, TaskStatus
from app.modules.schema.service import SYNC_TASK, enqueue_daily, sync_handler
from tests.a_group.test_datasource_schema_permission import draft, snapshot
from tests.fakes import MemoryQueue, MemoryTaskStore


async def test_sync_task_queue_progress_polling_duplicate_and_daily(identity_db):
    world = identity_db
    store, queue = MemoryTaskStore(), MemoryQueue()
    manager = TaskManager(
        store,
        queue,
        {SYNC_TASK: sync_handler(world.database, world.box, world.cache, world.inspector)},
    )
    world.inspector.snapshot = snapshot("id", "phone")
    async with world.database.transaction() as session:
        source = await world.datasource(session).create(draft(), actor=world.admin)
    async with world.database.transaction() as session:
        schema = world.schema(session)
        tasks = await enqueue_daily(schema, manager, actor=world.admin)
        assert len(tasks) == 1
        task = tasks[0]
        assert task.status == TaskStatus.QUEUED and queue.ids == [task.id]
        with pytest.raises(AppError):
            await schema.task(task.id, manager, actor=world.member)
    await manager.run(task.id)
    await manager.run(task.id)
    assert store.records[task.id].status == TaskStatus.SUCCESS
    assert store.records[task.id].progress == 100
    async with world.database.session() as session:
        catalog = await world.schema(session).catalog(source.id)
        assert {c.name for c in catalog[0].columns} == {"id", "phone"}
        assert not (
            await world.grants(session).permissions.visible_scope(world.member, source.id)
        ).tables
        assert (
            await world.schema(session).task(task.id, manager, actor=world.admin)
        ).status == "success"


@pytest.mark.parametrize("role", ["disabled", "demoted"])
async def test_queued_sync_rechecks_actor_identity(identity_db, role):
    world = identity_db
    from app.modules.org.schemas import UserUpdate, UserWrite

    async with world.database.transaction() as session:
        org = world.org(session)
        second = await org.create_user(
            UserWrite(username="second-admin", password=SecretStr("second-password"), role="admin"),
            actor=world.admin,
        )
        actor = await org.identity(second.id)
        source = await world.datasource(session).create(draft(), actor=world.admin)
        manager = TaskManager(
            MemoryTaskStore(),
            MemoryQueue(),
            {SYNC_TASK: sync_handler(world.database, world.box, world.cache, world.inspector)},
        )
        task = await world.schema(session).enqueue(source.id, manager, actor=actor)
        status: Literal["active", "disabled"] = "disabled" if role == "disabled" else "active"
        await org.update_user(
            actor.id,
            UserUpdate(role="member", team_id=world.team, status=status),
            actor=world.admin,
        )
    await manager.run(task.id)
    finished = await manager.store.get(task.id)
    assert finished.status == TaskStatus.FAILED
