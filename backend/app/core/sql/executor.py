import asyncio
import math
import time
from collections import defaultdict, deque
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.datasource import ConnectorRegistry, Datasource
from app.core.errors import AppError, DatasourceUnavailable, ErrorCode, SqlBlocked
from app.core.masking import mask_rows
from app.core.sql.guard import GuardedSql


class QueryLimits(BaseModel):
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_rows: int = Field(default=10000, ge=1, le=100000)
    max_cost: float = Field(default=100000, gt=0, allow_inf_nan=False)
    user_id: int = Field(ge=1)


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, object]]
    row_count: int
    truncated: bool
    duration_ms: int


class QueryLimiter(Protocol):
    async def acquire(self, user_id: int) -> str: ...
    async def release(self, user_id: int, lease: str) -> None: ...


class MemoryQueryLimiter:
    """For tests/single-process development; production injects RedisQueryLimiter."""

    def __init__(self, concurrent: int = 2, per_minute: int = 30) -> None:
        self.concurrent = concurrent
        self.per_minute = per_minute
        self.active: dict[int, set[str]] = defaultdict(set)
        self.history: dict[int, deque[float]] = defaultdict(deque)

    async def acquire(self, user_id: int) -> str:
        now = time.monotonic()
        history = self.history[user_id]
        while history and history[0] <= now - 60:
            history.popleft()
        if len(self.active[user_id]) >= self.concurrent or len(history) >= self.per_minute:
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
        self.redis, self.concurrent, self.per_minute = redis, concurrent, per_minute

    async def acquire(self, user_id: int) -> str:
        lease = str(uuid4())
        result = await cast(Any, self.redis).eval(
            self._ACQUIRE,
            2,
            f"query-rate:{{{user_id}}}",
            f"query-active:{{{user_id}}}",
            str(time.time()),
            str(self.per_minute),
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
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT)
        if sql.datasource_id != datasource.id or sql.dialect != datasource.dialect:
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        if not datasource.read_only:
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT)
        lease = await self.limiter.acquire(limits.user_id)
        start = time.monotonic()
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                connector = self.connectors.get(datasource.dialect)
                async with connector.read_session(datasource, limits.timeout_seconds) as session:
                    cost = await session.explain(sql.normalized_sql)
                    if not math.isfinite(cost) or cost > limits.max_cost:
                        raise SqlBlocked(ErrorCode.SQL_COST_EXCEEDED)
                    batch = await session.fetch(sql.normalized_sql, limits.max_rows + 1)
                    if len(batch.columns) != len(set(batch.columns)):
                        raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
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
            # Only SQL syntax/undefined-column errors may enter the correction loop.
            # Driver messages can contain secrets and invisible object names; discard them.
            sqlstate = getattr(error, "sqlstate", None)
            args = getattr(error, "args", ())
            # PostgreSQL (postgres/kingbase/gaussdb): sqlstate 42601 (syntax), 42703
            # (undefined column); MySQL: error code 1054 (unknown column), 1064
            # (syntax error); ClickHouse: DatabaseError with message containing error code
            if sqlstate in {"42601", "42703"} or (
                datasource.dialect == "mysql" and args and args[0] in {1054, 1064}
            ):
                raise AppError(ErrorCode.SQL_EXECUTION_ERROR) from None
            # ClickHouse DatabaseError treated as SQL_EXECUTION_ERROR for auto-correction
            if datasource.dialect == "clickhouse" and error.__class__.__name__ == "DatabaseError":
                raise AppError(ErrorCode.SQL_EXECUTION_ERROR) from None
            raise DatasourceUnavailable() from None
        finally:
            await asyncio.shield(self.limiter.release(limits.user_id, lease))
