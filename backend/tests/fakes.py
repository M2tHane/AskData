from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.datasource import (
    ConnectionTest,
    Connector,
    Datasource,
    MetadataSnapshot,
    QueryBatch,
    QuerySession,
)
from app.core.llm import (
    CallContext,
    CallRecord,
    ChatMessage,
    ChatResponse,
    ModelConfig,
    StreamChunk,
    Usage,
)
from app.core.permission import TableGrant, VisibleScope
from app.core.security import RefreshRecord
from app.core.tasks import TaskRecord, TaskStatus


class FakeConnector(Connector):
    def __init__(self, rows: list[dict[str, Any]] | None = None, cost: float = 1) -> None:
        self.rows = rows or []
        self.cost = cost
        self.executed: list[str] = []
        self.explained: list[str] = []
        self.opened = 0
        self.closed = 0
        self.error: Exception | None = None
        self.wait: Any = None

    @asynccontextmanager
    async def read_session(
        self, datasource: Datasource, timeout: float
    ) -> AsyncIterator[QuerySession]:
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    async def explain(self, sql: str) -> float:
        self.explained.append(sql)
        if self.wait:
            await self.wait.wait()
        return self.cost

    async def fetch(self, sql: str, max_rows: int) -> QueryBatch:
        if self.error:
            raise self.error
        self.executed.append(sql)
        return QueryBatch(
            columns=list(self.rows[0]) if self.rows else [], rows=self.rows[:max_rows]
        )

    async def metadata(self, datasource: Datasource) -> MetadataSnapshot:
        return MetadataSnapshot(tables=[])

    async def test(self, datasource: Datasource) -> ConnectionTest:
        return ConnectionTest(available=True, read_only_account=True)


class FakePermissions:
    def __init__(self, grants: list[TableGrant]) -> None:
        self.grants = grants
        self.calls = 0
        self.on_snapshot: Any = None

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]:
        self.calls += 1
        snapshot = [grant.model_copy(deep=True) for grant in self.grants]
        if self.on_snapshot:
            await self.on_snapshot()
        return snapshot


class MemoryScopeCache:
    def __init__(self) -> None:
        self.generation = 0
        self.values: dict[tuple, VisibleScope] = {}

    async def revision(self, datasource_id: int) -> str:
        return str(self.generation)

    async def get(self, user: Any, datasource_id: int, revision: str) -> VisibleScope | None:
        return self.values.get((user.id, user.role, user.team_id, datasource_id, revision))

    async def put(self, user: Any, scope: VisibleScope, revision: str) -> None:
        self.values[(user.id, user.role, user.team_id, scope.datasource_id, revision)] = scope

    async def invalidate(self, datasource_id: int) -> None:
        self.generation += 1


class MemoryRefreshStore:
    def __init__(self) -> None:
        self.records: dict[str, RefreshRecord] = {}

    async def save(self, record: RefreshRecord) -> None:
        self.records[record.token_hash] = record

    async def consume(self, token_hash: str, now: Any) -> RefreshRecord | None:
        record = self.records.get(token_hash)
        if record is None or record.revoked or record.expires_at <= now:
            return None
        record.revoked = True
        return record.model_copy()


class FakeModels:
    def __init__(self, models: list[ModelConfig]) -> None:
        self.models = models
        self.preferred: str | None = None

    async def configurations(self) -> list[ModelConfig]:
        return self.models

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        return self.preferred

    async def default(self, kind: str) -> str | None:
        return next((model.id for model in self.models if model.kind == kind), None)


class FakeUsage:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        self.block: Exception | None = None

    async def authorize(self, context: CallContext) -> None:
        if self.block:
            raise self.block

    async def record(self, record: CallRecord) -> None:
        self.records.append(record)


class FakeTransport:
    def __init__(self, text: str = "SELECT id FROM orders") -> None:
        self.text = text
        self.calls: list[str] = []
        self.fail_models: set[str] = set()
        self.fail_after_chunk = False

    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse:
        self.calls.append(model.id)
        if model.id in self.fail_models:
            raise RuntimeError("provider secret should never escape")
        return ChatResponse(text=self.text, usage=Usage(tokens_in=10, tokens_out=3))

    async def stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        await self.chat(model, messages)
        yield StreamChunk(text="SELECT ")
        if self.fail_after_chunk:
            raise RuntimeError("provider detail")
        yield StreamChunk(text="id FROM orders", usage=Usage(tokens_in=10, tokens_out=3))

    async def embed(self, model: ModelConfig, texts: list[str]) -> tuple[list[list[float]], Usage]:
        await self.chat(model, [])
        return [[0.1, 0.2] for _ in texts], Usage(tokens_in=len(texts))

    async def rerank(self, model: ModelConfig, query: str, documents: list[str]) -> list[int]:
        await self.chat(model, [])
        return list(reversed(range(len(documents))))


class MemoryTaskStore:
    def __init__(self) -> None:
        self.records: dict[str, TaskRecord] = {}

    async def create(self, task: TaskRecord) -> None:
        self.records[task.id] = task.model_copy(deep=True)

    async def get(self, task_id: str) -> TaskRecord | None:
        return self.records.get(task_id)

    async def transition(
        self,
        task_id: str,
        expected: TaskStatus,
        status: TaskStatus,
        progress: int | None = None,
        error: Any = None,
    ) -> bool:
        task = self.records[task_id]
        if task.status != expected:
            return False
        task.status, task.error = status, error
        if progress is not None:
            task.progress = progress
        return True

    async def progress(self, task_id: str, value: int) -> None:
        task = self.records[task_id]
        if task.status == TaskStatus.RUNNING:
            task.progress = max(value, task.progress)


class MemoryQueue:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.fail = False

    async def enqueue(self, task_id: str) -> None:
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.ids.append(task_id)
