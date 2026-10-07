from app import worker
from app.core.tasks import TaskStatus
from tests.fakes import MemoryQueue
from tests.test_db_audit_tasks_retention import database as database


async def test_worker_retention_queue_dispatch_shutdown(database, monkeypatch):
    queue = MemoryQueue()
    monkeypatch.setattr(worker, "ArqTaskQueue", lambda redis: queue)
    context = {"redis": object()}
    await worker.startup(context)
    try:
        await worker.schedule_retention(context)
        assert len(queue.ids) == 1
        await worker.dispatch_task(context, queue.ids[0])
        task = await context["tasks"].store.get(queue.ids[0])
        assert task.status == TaskStatus.SUCCESS and task.progress == 100
    finally:
        await worker.shutdown(context)
    assert worker.WorkerSettings.functions == [worker.dispatch_task]


def test_worker_has_no_insecure_default_database(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.delenv("ASKDATA_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert get_settings().database_url is None
    get_settings.cache_clear()
