import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from arq.connections import ArqRedis
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base
from app.core.errors import AppError, ErrorCode


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TaskRecord(BaseModel):
    id: str
    type: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    payload: dict[str, Any] = Field(default_factory=dict, repr=False)
    error: ErrorCode | None = None


class TaskRun(AuditFields, Base):
    __tablename__ = "task_run"
    task_id: Mapped[str] = mapped_column(String(36), unique=True)
    type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    progress: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC) + timedelta(days=90)
    )


class TaskStore(Protocol):
    async def create(self, task: TaskRecord) -> None: ...
    async def get(self, task_id: str) -> TaskRecord | None: ...
    async def transition(
        self,
        task_id: str,
        expected: TaskStatus,
        status: TaskStatus,
        progress: int | None = None,
        error: ErrorCode | None = None,
    ) -> bool:
        """Atomic compare-and-swap; terminal records cannot be restarted."""
        ...

    async def progress(self, task_id: str, value: int) -> None: ...


class DatabaseTaskStore:
    """Each operation commits independently so polling observes durable progress."""

    def __init__(self, sessions: Callable[[], AsyncSession]) -> None:
        self.sessions = sessions

    async def create(self, task: TaskRecord) -> None:
        async with self.sessions() as session, session.begin():
            session.add(
                TaskRun(
                    task_id=task.id,
                    type=task.type,
                    status=task.status,
                    progress=task.progress,
                    payload=task.payload,
                    error=task.error,
                )
            )

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self.sessions() as session:
            row = await session.scalar(select(TaskRun).where(TaskRun.task_id == task_id))
            return (
                TaskRecord(
                    id=row.task_id,
                    type=row.type,
                    status=TaskStatus(row.status),
                    progress=row.progress,
                    payload=row.payload,
                    error=ErrorCode(row.error) if row.error else None,
                )
                if row
                else None
            )

    async def transition(
        self,
        task_id: str,
        expected: TaskStatus,
        status: TaskStatus,
        progress: int | None = None,
        error: ErrorCode | None = None,
    ) -> bool:
        from sqlalchemy import update

        values: dict[str, Any] = {"status": status, "error": error}
        if progress is not None:
            values["progress"] = progress
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(TaskRun)
                .where(
                    TaskRun.task_id == task_id,
                    TaskRun.status == expected,
                )
                .values(**values)
                .returning(TaskRun.id)
            )
            return result.scalar_one_or_none() is not None

    async def progress(self, task_id: str, value: int) -> None:
        from sqlalchemy import update

        if not 0 <= value <= 100:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(TaskRun)
                .where(
                    TaskRun.task_id == task_id,
                    TaskRun.status == TaskStatus.RUNNING,
                    TaskRun.progress <= value,
                )
                .values(progress=value)
            )


class TaskQueue(Protocol):
    async def enqueue(self, task_id: str) -> None: ...


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def enqueue(self, task_id: str) -> None:
        await self.redis.enqueue_job("dispatch_task", task_id, _job_id=task_id)


class TaskContext:
    def __init__(self, task: TaskRecord, store: TaskStore) -> None:
        self.task, self.store = task, store

    async def progress(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        await self.store.progress(self.task.id, value)


TaskHandler = Callable[[TaskContext], Awaitable[None]]


class TaskManager:
    def __init__(
        self, store: TaskStore, queue: TaskQueue, handlers: dict[str, TaskHandler]
    ) -> None:
        self.store, self.queue, self.handlers = store, queue, handlers

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> TaskRecord:
        if kind not in self.handlers:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        task = TaskRecord(id=str(uuid4()), type=kind, payload=payload)
        await self.store.create(task)
        try:
            await self.queue.enqueue(task.id)
        except Exception:
            await self.store.transition(
                task.id, TaskStatus.QUEUED, TaskStatus.FAILED, error=ErrorCode.TASK_FAILED
            )
            raise AppError(ErrorCode.TASK_FAILED, http_status=503) from None
        return task

    async def run(self, task_id: str) -> None:
        task = await self.store.get(task_id)
        if task is None or not await self.store.transition(
            task_id,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        ):
            return
        try:
            await self.handlers[task.type](TaskContext(task, self.store))
        except asyncio.CancelledError:
            await asyncio.shield(
                self.store.transition(
                    task_id, TaskStatus.RUNNING, TaskStatus.FAILED, error=ErrorCode.QUERY_CANCELLED
                )
            )
            raise
        except Exception:
            await self.store.transition(
                task_id, TaskStatus.RUNNING, TaskStatus.FAILED, error=ErrorCode.TASK_FAILED
            )
        else:
            await self.store.transition(
                task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS, progress=100
            )
