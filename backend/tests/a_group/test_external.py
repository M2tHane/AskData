import secrets

import asyncpg
import pytest
from pydantic import SecretStr

from app.core.datasource import Datasource, DatasourceInspector
from app.core.sql.dialect import Dialect
from app.modules.datasource.schemas import DatasourceWrite
from app.modules.permission.schemas import ColumnGrantWrite, TableGrantWrite, TeamGrantsWrite
from tests.test_external_services import container, ready


@pytest.fixture
def postgres_container():
    password = secrets.token_urlsafe(32)
    with container(
        "postgres:16-alpine", [5432], {"POSTGRES_PASSWORD": password, "POSTGRES_DB": "askdata_a"}
    ) as ports:
        yield ports[5432], password


@pytest.mark.external
async def test_real_connection_privilege_warning_metadata_and_expiring_grants(
    identity_db, postgres_container
):
    world = identity_db
    port, password = postgres_container
    connection = await ready(
        lambda: asyncpg.connect(
            host="127.0.0.1", port=port, database="askdata_a", user="postgres", password=password
        )
    )
    world.inspector = DatasourceInspector()
    try:
        await connection.execute("""
            CREATE ROLE a_reader LOGIN PASSWORD 'ephemeral-reader-password';
            CREATE ROLE a_writer LOGIN PASSWORD 'ephemeral-writer-password';
            GRANT USAGE ON SCHEMA public TO a_reader;
            GRANT USAGE,CREATE ON SCHEMA public TO a_writer;
        """)
        writer = Datasource(
            id=1,
            dialect=Dialect.POSTGRES,
            host="127.0.0.1",
            port=port,
            database="askdata_a",
            username="a_writer",
            password=SecretStr("ephemeral-writer-password"),
            tls=False,
        )
        result = await world.inspector.test(writer)
        assert result.available and not result.read_only_account and result.warning
        reader = writer.model_copy(
            update={"username": "a_reader", "password": SecretStr("ephemeral-reader-password")}
        )
        assert (await world.inspector.test(reader)).read_only_account
        await connection.execute("""
            CREATE TABLE sales(id bigint PRIMARY KEY, phone text);
            COMMENT ON COLUMN sales.phone IS 'database phone';
            GRANT SELECT ON sales TO a_reader;
        """)
        source_input = DatasourceWrite(
            name="real-pg",
            type="postgresql",
            host=reader.host,
            port=reader.port,
            database=reader.database,
            username=reader.username,
            password=reader.password,
            tls=False,
        )
        async with world.database.transaction() as session:
            source = await world.datasource(session).create(source_input, actor=world.admin)
            assert source.read_only_account
            grants, schema = world.grants(session), world.schema(session)
            await schema.synchronize(source.id, grants, actor=world.admin)
            table = (await schema.catalog(source.id))[0]
            assert table.columns[0].is_pk and table.columns[1].comment == "database phone"
            await schema.describe_column(
                table.columns[0].id, "manual description", actor=world.admin
            )
            await grants.replace(
                source.id,
                world.team,
                TeamGrantsWrite(
                    tables=[
                        TableGrantWrite(
                            table_id=table.id,
                            columns=[ColumnGrantWrite(column_id=c.id) for c in table.columns],
                        )
                    ]
                ),
                actor=world.admin,
            )
        await connection.execute("ALTER TABLE sales DROP COLUMN phone")
        async with world.database.transaction() as session:
            grants, schema = world.grants(session), world.schema(session)
            await schema.synchronize(source.id, grants, actor=world.admin)
            scope = await grants.visible_scope(world.leader, source.id)
            assert scope.tables["public.sales"].columns == {"id"}
            assert (await schema.catalog(source.id))[0].columns[
                0
            ].description == "manual description"
    finally:
        await connection.close()
