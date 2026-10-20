"""arq entrypoint; API and worker use the same composition and task registry."""

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.composition import Composition
from app.core.config import get_settings
from app.core.db import Database
from app.core.deps import Role
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.core.tasks import ArqTaskQueue, DatabaseTaskStore, TaskManager
from app.modules.schema.service import enqueue_daily


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if settings.database_url is None:
        raise ValueError("ASKDATA_DATABASE_URL is required for worker")
    if settings.environment == "production":
        settings.require_secrets()
    configure_logging()
    database = Database(settings.database_url.get_secret_value())
    ctx["database"] = database
    try:
        runtime = Composition(settings, database, ctx["redis"], worker=True)
        # Keep queue construction at this entrypoint injectable for worker tests.
        runtime.tasks = TaskManager(
            DatabaseTaskStore(database.sessions), ArqTaskQueue(ctx["redis"]), runtime.handlers()
        )
        ctx["composition"], ctx["tasks"] = runtime, runtime.tasks
    except BaseException:
        await database.close()
        raise


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["database"].close()
    # arq owns and closes ctx['redis'] after this hook.


async def dispatch_task(ctx: dict[str, Any], task_id: str) -> None:
    await ctx["tasks"].run(task_id)


async def schedule_retention(ctx: dict[str, Any]) -> None:
    await ctx["tasks"].enqueue("retention", {})


async def schedule_daily_sync(ctx: dict[str, Any]) -> None:
    runtime: Composition = ctx["composition"]
    if runtime.settings.scheduler_admin_id is None:
        return
    async with runtime.services(transaction=True) as services:
        actor = await services.org.identity(runtime.settings.scheduler_admin_id)
        if actor.role != Role.ADMIN:
            raise AppError(ErrorCode.FORBIDDEN)
        await enqueue_daily(services.schema, runtime.tasks, actor=actor)


async def schedule_index_updates(ctx: dict[str, Any]) -> None:
    runtime: Composition = ctx["composition"]
    if runtime.settings.scheduler_admin_id is None:
        return
    async with runtime.services(transaction=True) as services:
        actor = await services.org.identity(runtime.settings.scheduler_admin_id)
        if actor.role != Role.ADMIN:
            raise AppError(ErrorCode.FORBIDDEN)
        for identifier in await services.datasource.active_ids():
            await services.embedding.enqueue(identifier, runtime.tasks, actor=actor)


class WorkerSettings:
    functions = [dispatch_task]
    cron_jobs = [
        cron(schedule_retention, hour=3, minute=0),
        cron(schedule_daily_sync, hour=2, minute=0),
        cron(schedule_index_updates, minute={5, 20, 35, 50}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    # One eval run is one job: 100 questions at 15-30 s each is 25-50 minutes. Each model call,
    # query and sync step has its own timeout; this only backstops a job that hangs outright.
    job_timeout = 3 * 3600
    max_jobs = 10
