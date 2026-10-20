from typing import Any

import pytest
from pydantic import SecretStr

from app.core import datasource
from app.core.sql.dialect import Dialect


@pytest.mark.parametrize("name", ["unknown", "postgresql", "upload", "mysql"])
def test_dialect_selection(name):
    if name == "unknown":
        with pytest.raises(ValueError):
            Dialect.from_name(name)
    else:
        assert Dialect.from_name(name) in Dialect


async def test_metadata_groups_tables_and_relations():
    rows = [
        {
            "schema_name": "public",
            "table_name": "orders",
            "name": "id",
            "data_type": "BIGINT",
            "ordinal": 1,
            "comment": "key",
            "table_comment": "orders",
            "is_pk": True,
        }
    ]
    snapshot = datasource._metadata(
        rows,
        [{"from_table": "orders", "from_column": "id", "to_table": "customers", "to_column": "id"}],
    )
    assert snapshot.tables[0].columns[0].is_pk
    assert snapshot.relations[0].to_table == "customers"


async def test_postgres_cursor_is_awaited_before_fetch():
    class Cursor:
        async def fetch(self, count):
            return [{"id": 1}]

    class Statement:
        async def cursor(self):
            return Cursor()

        def get_attributes(self):
            class Attribute:
                name = "id"

            return [Attribute()]

    class Connection:
        async def prepare(self, sql):
            return Statement()

    assert (await datasource._PostgresSession(Connection()).fetch("SELECT 1", 2)).rows == [
        {"id": 1}
    ]


async def test_mysql_unbuffered_results_are_not_drained():
    class Cursor:
        description = [("id",)]

        async def execute(self, sql):
            pass

        async def fetchmany(self, count):
            return [{"id": 1}]

        async def close(self):
            pytest.fail("Closing an unbuffered cursor would drain unbounded rows")

    class Connection:
        closed = False

        async def cursor(self, *args: Any):
            return Cursor()

        def close(self):
            self.closed = True

    connection = Connection()
    result = await datasource._MysqlSession(connection).fetch("SELECT 1", 2)
    assert result.rows == [{"id": 1}] and connection.closed


def test_new_dialect_names():
    """New dialects should be recognized by Dialect.from_name."""
    assert Dialect.from_name("kingbase") == Dialect.KINGBASE
    assert Dialect.from_name("gaussdb") == Dialect.GAUSSDB
    assert Dialect.from_name("clickhouse") == Dialect.CLICKHOUSE


def test_dialect_sqlglot_mapping():
    """Dialects should map correctly to sqlglot dialect names."""
    assert Dialect.MYSQL.sqlglot == "mysql"
    assert Dialect.POSTGRES.sqlglot == "postgres"
    assert Dialect.KINGBASE.sqlglot == "postgres"  # Kingbase is postgres-compatible
    assert Dialect.GAUSSDB.sqlglot == "postgres"  # GaussDB is postgres-compatible
    assert Dialect.CLICKHOUSE.sqlglot == "clickhouse"


class _ClickHouseResult:
    def __init__(self, columns, rows):
        self.column_names, self.result_rows = columns, rows


class _ClickHouseClient:
    def __init__(self, rows=None):
        self.queries, self.closed = [], False
        self.rows = rows if rows is not None else [(1, "Alice"), (2, "Bob")]

    async def query(self, sql, parameters=None):
        self.queries.append(sql)
        return _ClickHouseResult(["id", "name"], self.rows)

    async def close(self):
        self.closed = True


async def test_clickhouse_session_fetch_wraps_guarded_sql_with_row_cap():
    client = _ClickHouseClient()
    session = datasource._ClickHouseSession(client)
    batch = await session.fetch("SELECT id, name FROM users LIMIT 10", 100)
    # The guarded SQL keeps its own LIMIT; the row cap wraps it instead of appending.
    assert client.queries == ["SELECT * FROM (SELECT id, name FROM users LIMIT 10) LIMIT 101"]
    assert batch.columns == ["id", "name"]
    assert batch.rows == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


async def test_clickhouse_session_has_no_planner_cost():
    client = _ClickHouseClient()
    assert await datasource._ClickHouseSession(client).explain("SELECT 1") == 0.0
    assert client.queries == []


def _fake_clickhouse(monkeypatch, client):
    import sys
    import types

    calls = []

    async def get_async_client(**kwargs):
        calls.append(kwargs)
        return client

    module = types.ModuleType("clickhouse_connect")
    module.get_async_client = get_async_client
    monkeypatch.setitem(sys.modules, "clickhouse_connect", module)
    return calls


def _clickhouse_source():
    return datasource.Datasource(
        id=1,
        dialect=Dialect.CLICKHOUSE,
        host="ch",
        port=8123,
        database="shop",
        username="reader",
        password=SecretStr("pw"),
    )


async def test_clickhouse_read_session_is_read_only_with_server_timeout(monkeypatch):
    client = _ClickHouseClient()
    calls = _fake_clickhouse(monkeypatch, client)
    connector = datasource.ClickHouseConnector()
    async with connector.read_session(_clickhouse_source(), 12) as session:
        await session.fetch("SELECT 1 AS id", 5)
    assert calls[0]["settings"] == {"readonly": "2", "max_execution_time": 12}
    assert client.closed


@pytest.mark.parametrize("profile_readonly,expected", [(1, True), (2, True), (0, False)])
async def test_clickhouse_connection_test_reads_account_profile(
    monkeypatch, profile_readonly, expected
):
    client = _ClickHouseClient(rows=[(profile_readonly,)])
    calls = _fake_clickhouse(monkeypatch, client)
    result = await datasource.ClickHouseConnector().test(_clickhouse_source())
    # No client-side settings: the readonly value comes from the account, not the session.
    assert calls[0]["settings"] == {}
    assert result.available and result.read_only_account is expected
