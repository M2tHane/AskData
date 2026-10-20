"""Real driver integration, opt in with pytest --external-services.

Every container has a unique name and is removed in a fixture finally block.
No existing service, credentials or volume is reused.
"""

import asyncio
import json
import os
import secrets
import subprocess
import time
from contextlib import contextmanager

import aiomysql
import asyncpg
import pytest
from minio import Minio
from pydantic import SecretStr
from redis.asyncio import Redis

from app.core.datasource import ConnectorRegistry, Datasource, MysqlConnector, PostgresConnector
from app.core.errors import AppError
from app.core.permission import (
    PermissionService,
    RedisScopeCache,
    TableGrant,
    TableScope,
    VisibleScope,
)
from app.core.sql.dialect import Dialect
from app.core.sql.executor import QueryLimits, RedisQueryLimiter, SqlExecutor
from app.core.storage import MinioStore
from tests.fakes import FakePermissions

pytestmark = pytest.mark.external


@contextmanager
def container(image, ports, environment, command=()):
    name = "askdata-core-test-" + secrets.token_hex(6)
    arguments = ["docker", "run", "--rm", "-d", "--name", name]
    for port in ports:
        arguments.extend(["-p", f"127.0.0.1::{port}"])
    for key in environment:
        arguments.extend(["-e", key])
    arguments += [image, *command]
    subprocess.run(
        arguments, env={**os.environ, **environment}, check=True, capture_output=True, timeout=30
    )
    try:
        inspect = subprocess.run(
            ["docker", "inspect", name], check=True, capture_output=True, text=True, timeout=10
        )
        mapping = json.loads(inspect.stdout)[0]["NetworkSettings"]["Ports"]
        yield {port: int(mapping[f"{port}/tcp"][0]["HostPort"]) for port in ports}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=True, timeout=20)


async def ready(connect):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            return await connect()
        except Exception:
            await asyncio.sleep(0.25)
    pytest.fail("Disposable database did not become ready")


async def test_real_postgres_guard_executor_metadata_and_readonly(guard):
    password = secrets.token_urlsafe(32)
    with container(
        "postgres:16-alpine", [5432], {"POSTGRES_PASSWORD": password, "POSTGRES_DB": "askdata"}
    ) as ports:
        port = ports[5432]
        connection = await ready(
            lambda: asyncpg.connect(
                host="127.0.0.1", port=port, database="askdata", user="postgres", password=password
            )
        )
        try:
            await connection.execute("""
                CREATE TABLE orders(id bigint PRIMARY KEY, tenant_id bigint,
                                    phone text, secret text);
                INSERT INTO orders VALUES (1,7,'13812345678','hidden'),(2,8,'13912345678','hidden');
                CREATE ROLE core_reader LOGIN PASSWORD 'ephemeral-integration-reader';
                GRANT USAGE ON SCHEMA public TO core_reader;
                GRANT SELECT ON orders TO core_reader;
            """)
            connector = PostgresConnector()
            admin = Datasource(
                id=1,
                dialect=Dialect.POSTGRES,
                host="127.0.0.1",
                port=port,
                database="askdata",
                username="postgres",
                password=SecretStr(password),
                tls=False,
            )
            reader = admin.model_copy(
                update={
                    "username": "core_reader",
                    "password": SecretStr("ephemeral-integration-reader"),
                }
            )
            assert not (await connector.test(admin)).read_only_account
            assert (await connector.test(reader)).read_only_account
            metadata = await connector.metadata(reader)
            assert metadata.tables[0].columns[0].is_pk
            scope = VisibleScope(
                datasource_id=1,
                tables={
                    "public.orders": TableScope(
                        all_columns={
                            "id": "BIGINT",
                            "tenant_id": "BIGINT",
                            "phone": "TEXT",
                            "secret": "TEXT",
                        },
                        columns={"id", "phone"},
                    )
                },
            )
            sql = guard.check("SELECT id FROM public.orders", "postgres", scope)
            result = await SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector})).run(
                reader,
                sql,
                QueryLimits(user_id=3),
            )
            assert sorted(row["id"] for row in result.rows) == [1, 2]
            # A privileged login still cannot write inside the connector's transaction.
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                async with connector.read_session(admin, 10) as session:
                    await session.connection.execute("DELETE FROM orders")
        finally:
            await connection.close()


async def test_real_mysql_readonly_metadata_cost_and_truncation(guard):
    password = secrets.token_urlsafe(32)
    with container(
        "mysql:8",
        [3306],
        {"MYSQL_ROOT_PASSWORD": password, "MYSQL_ROOT_HOST": "%", "MYSQL_DATABASE": "askdata"},
    ) as ports:
        port = ports[3306]
        connection = await ready(
            lambda: aiomysql.connect(
                host="127.0.0.1",
                port=port,
                db="askdata",
                user="root",
                password=password,
                autocommit=True,
            )
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("CREATE TABLE orders(id BIGINT PRIMARY KEY, phone TEXT)")
                await cursor.execute(
                    "INSERT INTO orders VALUES (1,'13812345678'), (2,'13912345678')"
                )
                await cursor.execute(
                    "CREATE USER 'core_reader'@'%' IDENTIFIED BY 'ephemeral-integration-reader'"
                )
                await cursor.execute("GRANT SELECT ON askdata.* TO 'core_reader'@'%'")
            connector = MysqlConnector()
            admin = Datasource(
                id=1,
                dialect=Dialect.MYSQL,
                host="127.0.0.1",
                port=port,
                database="askdata",
                username="root",
                password=SecretStr(password),
                tls=False,
            )
            reader = admin.model_copy(
                update={
                    "username": "core_reader",
                    "password": SecretStr("ephemeral-integration-reader"),
                }
            )
            assert (await connector.test(reader)).read_only_account
            assert not (await connector.test(admin)).read_only_account
            assert (await connector.metadata(reader)).tables[0].columns[0].is_pk
            scope = VisibleScope(
                datasource_id=1,
                tables={
                    "orders": TableScope(
                        all_columns={"id": "BIGINT", "phone": "TEXT"},
                        columns={"id", "phone"},
                    )
                },
            )
            result = await SqlExecutor(ConnectorRegistry({Dialect.MYSQL: connector})).run(
                reader,
                guard.check("SELECT id FROM orders ORDER BY id", "mysql", scope),
                QueryLimits(user_id=3, max_rows=1),
            )
            assert result.truncated and result.rows == [{"id": 1}]
            with pytest.raises(aiomysql.OperationalError):
                async with connector.read_session(admin, 10) as session:
                    async with session.connection.cursor() as cursor:
                        await cursor.execute("DELETE FROM orders")
        finally:
            connection.close()


async def test_real_redis_scope_invalidation_and_distributed_limits(member):
    with container("redis:7-alpine", [6379], {}) as ports:
        redis = Redis(host="127.0.0.1", port=ports[6379])
        try:
            await ready(redis.ping)
            cache = RedisScopeCache(redis)
            provider = FakePermissions(
                [
                    TableGrant(
                        name="orders",
                        all_columns={"id": "INT"},
                        team_visible=True,
                        member_visible=True,
                        team_columns={"id"},
                        member_columns={"id"},
                    )
                ]
            )
            service = PermissionService(provider, cache)
            assert (await service.visible_scope(member, 1)).tables
            assert (await service.visible_scope(member, 1)).tables
            assert provider.calls == 1
            provider.grants.clear()
            await service.invalidate(1)
            assert not (await service.visible_scope(member, 1)).tables
            limiter = RedisQueryLimiter(redis, concurrent=1, per_minute=2)
            lease = await limiter.acquire(3)
            with pytest.raises(AppError):
                await RedisQueryLimiter(redis, concurrent=1).acquire(3)
            await limiter.release(3, lease)
            await limiter.release(3, await limiter.acquire(3))
            with pytest.raises(AppError):
                await limiter.acquire(3)
            await limiter.release(3, await limiter.acquire(3, "replay"))
        finally:
            await redis.aclose()


async def test_real_minio_upload_download_remove():
    password = secrets.token_urlsafe(32)
    with container(
        "minio/minio:latest",
        [9000],
        {"MINIO_ROOT_USER": "coretest", "MINIO_ROOT_PASSWORD": password},
        ["server", "/data"],
    ) as ports:
        client = Minio(
            f"127.0.0.1:{ports[9000]}", access_key="coretest", secret_key=password, secure=False
        )

        async def connect():
            return await asyncio.to_thread(client.list_buckets)

        await ready(connect)
        await asyncio.to_thread(client.make_bucket, "coretest")
        store = MinioStore(client, "coretest")
        await store.put("uploads/team_1/ds_1/v1/orders.csv", b"id\n1", "text/csv")
        assert await store.get("uploads/team_1/ds_1/v1/orders.csv") == b"id\n1"
        await store.delete("uploads/team_1/ds_1/v1/orders.csv")
        assert not list(await asyncio.to_thread(lambda: list(client.list_objects("coretest"))))
