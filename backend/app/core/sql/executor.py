import asyncio
import math
import time
from collections import defaultdict, deque
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.datasource import ConnectorRegistry, Datasource
from app.core.errors import (
    AppError,
    BlockRule,
    DatasourceUnavailable,
    ErrorCode,
    SqlBlocked,
    SqlExecutionFailed,
)
from app.core.masking import mask_rows
from app.core.sql.guard import GuardedSql

# MySQL errors in the SQL itself: ambiguous/unknown column, grouping, syntax, mixed aggregates.
_MYSQL_SQL_ERRORS = {1052, 1054, 1055, 1064, 1111, 1140}


class QueryLimits(BaseModel):
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_rows: int = Field(default=10000, ge=1, le=100000)
    max_cost: float = Field(default=100000, gt=0, allow_inf_nan=False)
    user_id: int = Field(ge=1)
    # Re-reading an earlier answer's rows has its own per-minute budget so that opening
    # conversations never uses up the user's questions; concurrency is shared.
    purpose: Literal["query", "replay"] = "query"


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, object]]
    row_count: int
    truncated: bool
    duration_ms: int


type Purpose = Literal["query", "replay"]
REPLAY_PER_MINUTE = 120


class QueryLimiter(Protocol):
    async def acquire(self, user_id: int, purpose: Purpose = "query") -> str: ...
    async def release(self, user_id: int, lease: str) -> None: ...


class MemoryQueryLimiter:
    """For tests/single-process development; production injects RedisQueryLimiter."""

    def __init__(self, concurrent: int = 2, per_minute: int = 30) -> None:
        self.concurrent = concurrent
        self.per_minute = {"query": per_minute, "replay": REPLAY_PER_MINUTE}
        self.active: dict[int, set[str]] = defaultdict(set)
        self.history: dict[tuple[int, str], deque[float]] = defaultdict(deque)

    async def acquire(self, user_id: int, purpose: Purpose = "query") -> str:
        now = time.monotonic()
        history = self.history[(user_id, purpose)]
        while history and history[0] <= now - 60:
            history.popleft()
        if len(self.active[user_id]) >= self.concurrent or len(history) >= self.per_minute[purpose]:
            raise AppError(ErrorCode.RATE_LIMITED, http_status=429)
        lease = str(uuid4())
        self.active[user_id].add(lease)
        history.append(now)
        return lease

    async def release(self, user_id: int, lease: str) -> None:
        self.active[user_id].discard(lease)


class RedisQueryLimiter:
    _ACQUIRE = """
    local now = tonumber(ARGV[1])
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-60)
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
    if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) or
       redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
    redis.call('ZADD', KEYS[1], now, ARGV[4])
    redis.call('ZADD', KEYS[2], now+360, ARGV[4])
    redis.call('EXPIRE', KEYS[1], 360)
    redis.call('EXPIRE', KEYS[2], 360)
    return 1
    """

    def __init__(self, redis: Redis, concurrent: int = 2, per_minute: int = 30) -> None:
        self.redis, self.concurrent = redis, concurrent
        self.per_minute = {"query": per_minute, "replay": REPLAY_PER_MINUTE}

    async def acquire(self, user_id: int, purpose: Purpose = "query") -> str:
        lease = str(uuid4())
        result = await cast(Any, self.redis).eval(
            self._ACQUIRE,
            2,
            f"{purpose}-rate:{{{user_id}}}",
            f"query-active:{{{user_id}}}",
            str(time.time()),
            str(self.per_minute[purpose]),
            str(self.concurrent),
            lease,
        )
        if not result:
            raise AppError(ErrorCode.RATE_LIMITED, http_status=429)
        return lease

    async def release(self, user_id: int, lease: str) -> None:
        await self.redis.zrem(f"query-active:{{{user_id}}}", lease)


class SqlExecutor:
    @classmethod
    def default(cls, redis: Redis | None = None) -> "SqlExecutor":
        """Construct private connectors and, when available, distributed limits inside core."""
        return cls(ConnectorRegistry(), RedisQueryLimiter(redis) if redis is not None else None)

    def __init__(self, connectors: ConnectorRegistry, limiter: QueryLimiter | None = None) -> None:
        self.connectors = connectors
        self.limiter = limiter or MemoryQueryLimiter()

    async def run(
        self, datasource: Datasource, sql: GuardedSql, limits: QueryLimits
    ) -> QueryResult:
        if not isinstance(sql, GuardedSql) or not sql.authentic():
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT, BlockRule.NOT_GUARDED)
        if sql.datasource_id != datasource.id or sql.dialect != datasource.dialect:
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.DATASOURCE_MISMATCH)
        if not datasource.read_only:
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT, BlockRule.NOT_READ_ONLY)
        lease = await self.limiter.acquire(limits.user_id, limits.purpose)
        start = time.monotonic()
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                connector = self.connectors.get(datasource.dialect)
                async with connector.read_session(datasource, limits.timeout_seconds) as session:
                    cost = await session.explain(sql.normalized_sql)
                    if not math.isfinite(cost) or cost > limits.max_cost:
                        raise SqlBlocked(ErrorCode.SQL_COST_EXCEEDED, BlockRule.COST_EXCEEDED)
                    batch = await session.fetch(sql.normalized_sql, limits.max_rows + 1)
                    if len(batch.columns) != len(set(batch.columns)):
                        raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.DUPLICATE_OUTPUT)
                    rows = mask_rows(batch.rows[: limits.max_rows], sql.output_masks)
                    return QueryResult(
                        columns=batch.columns,
                        rows=rows,
                        row_count=len(rows),
                        truncated=len(batch.rows) > limits.max_rows,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
        except TimeoutError:
            raise AppError(ErrorCode.QUERY_TIMEOUT, http_status=408) from None
        except AppError:
            raise
        except asyncio.CancelledError:
            # Propagate cancellation so request/worker cancellation can tear down connections.
            raise
        except Exception as error:
            # SQL errors (class 42 syntax/rule violations except missing privilege, class 22
            # data exceptions) may enter the correction loop; anything else is the source.
            # Driver messages can contain secrets and invisible object names; only the
            # standard error code is kept.
            sqlstate = getattr(error, "sqlstate", None)
            args = getattr(error, "args", ())
            code = args[0] if args and isinstance(args[0], int) else None
            if isinstance(sqlstate, str) and sqlstate[:2] in {"42", "22"} and sqlstate != "42501":
                raise SqlExecutionFailed(sqlstate) from None
            if datasource.dialect == "mysql" and code in _MYSQL_SQL_ERRORS:
                raise SqlExecutionFailed(code) from None
            # ClickHouse DatabaseError treated as SQL_EXECUTION_ERROR for auto-correction
            if datasource.dialect == "clickhouse" and error.__class__.__name__ == "DatabaseError":
                raise SqlExecutionFailed() from None
            raise DatasourceUnavailable(sqlstate if sqlstate is not None else code) from None
        finally:
            await asyncio.shield(self.limiter.release(limits.user_id, lease))
