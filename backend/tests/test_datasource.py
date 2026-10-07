from typing import Any

import pytest

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
