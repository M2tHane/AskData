import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect

from app.core.datasource import Datasource, DatasourceInspector, _PostgresInspectionConnector
from app.core.sql.dialect import Dialect

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("writable", [False, True])
async def test_postgres_inspection_covers_empty_schema_create_privilege(monkeypatch, writable):
    class Connection:
        closed = False
        query = ""

        async def fetchval(self, query, schema):
            self.query = query
            assert schema == "public"
            return writable

        async def close(self):
            self.closed = True

    connection = Connection()

    async def connect(self, datasource):
        return connection

    monkeypatch.setattr(_PostgresInspectionConnector, "_connect", connect)
    result = await DatasourceInspector().test(
        Datasource(
            id=1,
            dialect=Dialect.POSTGRES,
            host="unused",
            port=5432,
            database="unused",
            username="readonly",
            password=SecretStr("never-log"),
        )
    )
    assert result.available and result.read_only_account is not writable
    assert bool(result.warning) is writable
    assert connection.closed
    assert "SELECT has_schema_privilege($1, 'CREATE')" in connection.query
    assert "rolbypassrls" in connection.query and "has_database_privilege" in connection.query


async def test_identity_migration_round_trip(identity_db):
    world = identity_db
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    async with world.database.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    owned = {
        "team",
        "user",
        "refresh_token",
        "datasource",
        "meta_table",
        "meta_column",
        "meta_relation",
        "team_table_grant",
        "team_column_grant",
        "model_config",
        "team_model_pref",
    }
    assert owned <= names
    await asyncio.to_thread(command.downgrade, config, "0001_core")
    async with world.database.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert not names & owned
    assert {"audit_log", "task_run", "query_run"} <= names
    await asyncio.to_thread(command.upgrade, config, "0002")
    async with world.database.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert owned <= names
