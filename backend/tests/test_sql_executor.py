import asyncio

import pytest
from pydantic import SecretStr

from app.core.datasource import ConnectorRegistry, Datasource
from app.core.errors import AppError, ErrorCode
from app.core.sql.dialect import Dialect
from app.core.sql.executor import MemoryQueryLimiter, QueryLimits, SqlExecutor
from tests.fakes import FakeConnector


@pytest.fixture
def datasource():
    return Datasource(
        id=1,
        dialect=Dialect.POSTGRES,
        host="localhost",
        port=5432,
        database="test",
        username="readonly",
        password=SecretStr("never-log"),
    )


async def test_normalized_execution_truncation_and_masking(guard, scope, datasource):
    connector = FakeConnector([{"p": "13812345678", "id": n} for n in range(3)])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    sql = guard.check("select phone as p, id from orders -- model comment", "postgres", scope)
    result = await executor.run(datasource, sql, QueryLimits(user_id=3, max_rows=2))
    assert result.truncated and result.row_count == 2
    assert result.rows[0]["p"] == "138****5678"
    assert connector.executed == [sql.normalized_sql] == connector.explained
    assert "comment" not in connector.executed[0]
    assert connector.opened == connector.closed == 1


@pytest.mark.parametrize("kind", ["raw", "tampered", "datasource", "dialect", "write-account"])
async def test_rejection_never_connects(guard, scope, datasource, kind):
    connector = FakeConnector()
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    sql = guard.check("SELECT id FROM orders", "postgres", scope)
    if kind == "raw":
        sql = "SELECT id FROM orders"
    elif kind == "tampered":
        sql = sql.model_copy(update={"normalized_sql": "DROP TABLE orders"})
    elif kind == "datasource":
        datasource = datasource.model_copy(update={"id": 2})
    elif kind == "dialect":
        datasource = datasource.model_copy(update={"dialect": Dialect.MYSQL})
    else:
        datasource = datasource.model_copy(update={"read_only": False})
    with pytest.raises(AppError):
        await executor.run(datasource, sql, QueryLimits(user_id=3))
    assert connector.opened == 0 and not connector.executed


@pytest.mark.parametrize("cost", [100001, float("inf"), float("nan")])
async def test_cost_prevents_fetch(guard, scope, datasource, cost):
    connector = FakeConnector(cost=cost)
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    sql = guard.check("SELECT id FROM orders", "postgres", scope)
    with pytest.raises(AppError) as caught:
        await executor.run(datasource, sql, QueryLimits(user_id=3))
    assert caught.value.code == ErrorCode.SQL_COST_EXCEEDED
    assert not connector.executed and connector.closed == 1


async def test_timeout_closes_connection_and_releases_limit(guard, scope, datasource):
    connector = FakeConnector()
    connector.wait = asyncio.Event()
    limiter = MemoryQueryLimiter(concurrent=1)
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}), limiter)
    with pytest.raises(AppError) as caught:
        await executor.run(
            datasource,
            guard.check("SELECT id FROM orders", "postgres", scope),
            QueryLimits(user_id=3, timeout_seconds=0.01),
        )
    assert caught.value.code == ErrorCode.QUERY_TIMEOUT
    assert connector.closed == 1 and not limiter.active[3]


async def test_cancellation_propagates_and_cleans_up(guard, scope, datasource):
    connector = FakeConnector()
    connector.wait = asyncio.Event()
    limiter = MemoryQueryLimiter(concurrent=1)
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}), limiter)
    task = asyncio.create_task(
        executor.run(
            datasource,
            guard.check("SELECT id FROM orders", "postgres", scope),
            QueryLimits(user_id=3),
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert connector.closed == 1 and not limiter.active[3]


async def test_provider_error_does_not_escape(guard, scope, datasource):
    connector = FakeConnector()
    connector.error = RuntimeError("password=secret hidden_table unknown_column")
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    with pytest.raises(AppError) as caught:
        await executor.run(
            datasource,
            guard.check("SELECT id FROM orders", "postgres", scope),
            QueryLimits(user_id=3),
        )
    assert caught.value.code == ErrorCode.DATASOURCE_UNAVAILABLE
    assert "secret" not in caught.value.message and "hidden_table" not in caught.value.message


async def test_concurrency_and_frequency_limits():
    limiter = MemoryQueryLimiter(concurrent=1, per_minute=2)
    lease = await limiter.acquire(3)
    with pytest.raises(AppError) as caught:
        await limiter.acquire(3)
    assert caught.value.code == ErrorCode.RATE_LIMITED
    await limiter.release(3, lease)
    lease = await limiter.acquire(3)
    await limiter.release(3, lease)
    with pytest.raises(AppError):
        await limiter.acquire(3)
    assert await limiter.acquire(4)


@pytest.mark.parametrize(
    ("sqlstate", "code", "subject", "hint"),
    [
        ("42803", ErrorCode.SQL_EXECUTION_ERROR, "sqlstate_42803", "GROUP BY"),
        ("22012", ErrorCode.SQL_EXECUTION_ERROR, "sqlstate_22012", "NULLIF"),
        ("42P10", ErrorCode.SQL_EXECUTION_ERROR, "sqlstate_42p10", ""),
        # Missing privilege and connection failures are the source, not the SQL.
        ("42501", ErrorCode.DATASOURCE_UNAVAILABLE, "sqlstate_42501", None),
        ("08006", ErrorCode.DATASOURCE_UNAVAILABLE, "sqlstate_08006", None),
        (None, ErrorCode.DATASOURCE_UNAVAILABLE, None, None),
    ],
)
async def test_database_errors_keep_only_the_standard_code(
    guard, scope, datasource, sqlstate, code, subject, hint
):
    class DriverError(Exception):
        pass

    connector = FakeConnector()
    connector.error = DriverError("password=secret hidden_table unknown_column")
    connector.error.sqlstate = sqlstate  # type: ignore[attr-defined]
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    with pytest.raises(AppError) as caught:
        await executor.run(
            datasource,
            guard.check("SELECT id FROM orders", "postgres", scope),
            QueryLimits(user_id=3),
        )
    error = caught.value
    assert error.code == code and getattr(error, "subject", None) == subject
    if hint is not None:
        assert hint in getattr(error, "detail", "")
    assert "secret" not in error.message and "hidden_table" not in error.message
    assert "hidden_table" not in getattr(error, "detail", "")
