"""Disposable PG integration: uses the actual worker-only gateway and query channel."""

import asyncio
import secrets
import subprocess
import time
from uuid import uuid4

import asyncpg
import pytest
from pydantic import SecretStr

from app.core.datasource import ConnectorRegistry, Datasource, PostgresConnector
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError
from app.core.importer import ImportColumn, ImportPlan, PostgresImportGateway
from app.core.permission import PermissionService, TableGrant
from app.core.sql.dialect import Dialect
from app.core.sql.executor import QueryLimits, SqlExecutor
from app.core.sql.guard import SqlGuard
from tests.fakes import FakePermissions


@pytest.fixture
async def pg_import():
    name, password = f"askdata-d-pg-{uuid4().hex[:10]}", secrets.token_urlsafe(24)
    await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=imports",
            "-p",
            "127.0.0.1::5432",
            "postgres:18-alpine",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        port = int(
            (
                await asyncio.to_thread(
                    subprocess.run,
                    ["docker", "port", name, "5432"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        deadline = time.monotonic() + 30
        admin = None
        while time.monotonic() < deadline:
            try:
                admin = await asyncpg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="postgres",
                    database="imports",
                    password=password,
                    ssl=False,
                )
                break
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.2)
        assert admin is not None
        writer_password = secrets.token_urlsafe(24)
        await admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await admin.execute("REVOKE ALL ON DATABASE imports FROM PUBLIC")
        await admin.execute(
            f"CREATE ROLE writer LOGIN CREATEROLE NOINHERIT PASSWORD '{writer_password}'"
        )
        await admin.execute("GRANT CONNECT ON DATABASE imports TO writer WITH GRANT OPTION")
        await admin.execute("GRANT CREATE ON DATABASE imports TO writer")
        writer = Datasource(
            id=1,
            dialect=Dialect.POSTGRES,
            host="127.0.0.1",
            port=port,
            database="imports",
            username="writer",
            password=SecretStr(writer_password),
            read_only=False,
            tls=False,
        )
        yield PostgresImportGateway(writer, SecretStr(secrets.token_urlsafe(24))), admin
        await admin.close()
    finally:
        await asyncio.to_thread(
            subprocess.run, ["docker", "rm", "-f", name], capture_output=True, timeout=30
        )


@pytest.mark.external
async def test_postgres_copy_rollback_append_and_schema_account_isolation(pg_import):
    gateway, admin = pg_import
    columns = [ImportColumn(name="id", data_type="integer"), ImportColumn(name="code")]
    plan = ImportPlan(datasource_id=7, table_name="sales", columns=columns, batch_size=2)
    async with gateway.transaction(plan) as transaction:
        assert await transaction.write([(1, "00123"), (2, "00234"), (3, "00345")]) == 3
    # A COPY error in the second batch must restore the table dropped by overwrite.
    with pytest.raises(AppError):
        async with gateway.transaction(plan) as transaction:
            await transaction.write([(9, "ok"), (10, "ok"), (2**70, "invalid")])
    assert await admin.fetchval("SELECT count(*) FROM ds_7.sales") == 3
    append = plan.model_copy(update={"mode": "append"})
    async with gateway.transaction(append) as transaction:
        await transaction.write([(4, "00456")])
    with pytest.raises(AppError):
        async with gateway.transaction(
            append.model_copy(
                update={
                    "columns": [ImportColumn(name="different")],
                }
            )
        ):
            pytest.fail("Append with a changed schema was accepted")
    assert await admin.fetchval("SELECT count(*) FROM ds_7.sales") == 4
    # A second source receives a distinct query login and no reciprocal grants.
    async with gateway.transaction(plan.model_copy(update={"datasource_id": 8})) as transaction:
        await transaction.write([(5, "00567")])
    ds = gateway.query_datasource(7)
    permissions = PermissionService(
        FakePermissions(
            [
                TableGrant(
                    name="ds_7.sales",
                    all_columns={"id": "BIGINT", "code": "TEXT"},
                )
            ]
        )
    )
    actor = CurrentUser(id=1, role=Role.ADMIN)
    scope = await permissions.visible_scope(actor, 7)
    guarded = SqlGuard().check("SELECT id, code FROM ds_7.sales ORDER BY id", "postgres", scope)
    outcome = await SqlExecutor(ConnectorRegistry()).run(ds, guarded, QueryLimits(user_id=1))
    assert outcome.row_count == 4 and outcome.rows[0]["code"] == "00123"
    assert (await PostgresConnector().test(ds)).read_only_account
    reader = await asyncpg.connect(
        host=ds.host,
        port=ds.port,
        database=ds.database,
        user=ds.username,
        password=ds.password.get_secret_value(),
        ssl=False,
    )
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.fetch("SELECT * FROM ds_8.sales")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("INSERT INTO ds_7.sales VALUES (99, 'x')")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("CREATE TABLE ds_7.stolen(id int)")
    finally:
        await reader.close()
