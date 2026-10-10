import json
import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import aiomysql
import asyncpg
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.errors import DatasourceUnavailable
from app.core.sql.dialect import Dialect


class Datasource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    dialect: Dialect
    host: str
    port: int = Field(ge=1, le=65535)
    database: str
    username: str
    password: SecretStr
    schema_name: str = "public"
    read_only: bool = True
    tls: bool = True


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    comment: str = ""
    ordinal: int
    is_pk: bool = False


class TableMetadata(BaseModel):
    schema_name: str
    name: str
    comment: str = ""
    columns: list[ColumnMetadata]


class RelationMetadata(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class MetadataSnapshot(BaseModel):
    tables: list[TableMetadata]
    relations: list[RelationMetadata] = Field(default_factory=list)


class ConnectionTest(BaseModel):
    available: bool
    read_only_account: bool
    warning: str | None = None


class QueryBatch(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]


class QuerySession(Protocol):
    async def explain(self, sql: str) -> float: ...
    async def fetch(self, sql: str, max_rows: int) -> QueryBatch: ...


class Connector(ABC):
    @abstractmethod
    def read_session(self, datasource: Datasource, timeout: float) -> Any:
        """Async context manager; transaction MUST be read-only and rolled back on exit."""

    @abstractmethod
    async def metadata(self, datasource: Datasource) -> MetadataSnapshot: ...

    @abstractmethod
    async def test(self, datasource: Datasource) -> ConnectionTest: ...


class _PostgresSession:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def explain(self, sql: str) -> float:
        value = await self.connection.fetchval("EXPLAIN (FORMAT JSON) " + sql)
        plan = json.loads(value) if isinstance(value, str) else value
        return float(plan[0]["Plan"]["Total Cost"])

    async def fetch(self, sql: str, max_rows: int) -> QueryBatch:
        statement = await self.connection.prepare(sql)
        cursor = await statement.cursor()
        rows = await cursor.fetch(max_rows)
        return QueryBatch(
            columns=[attr.name for attr in statement.get_attributes()],
            rows=[dict(row) for row in rows],
        )


class PostgresConnector(Connector):
    async def _connect(self, ds: Datasource, timeout: float = 30) -> Any:
        return await asyncpg.connect(
            host=ds.host,
            port=ds.port,
            database=ds.database,
            user=ds.username,
            password=ds.password.get_secret_value(),
            ssl=True if ds.tls else False,
            timeout=timeout,
            command_timeout=timeout,
            server_settings={"search_path": '"' + ds.schema_name.replace('"', '""') + '"'},
        )

    @asynccontextmanager
    async def read_session(
        self, datasource: Datasource, timeout: float
    ) -> AsyncIterator[QuerySession]:
        connection = await self._connect(datasource, timeout)
        try:
            async with connection.transaction(readonly=True):
                await connection.execute(
                    "SET LOCAL statement_timeout = " + str(int(timeout * 1000))
                )
                yield _PostgresSession(connection)
        finally:
            await connection.close()

    async def metadata(self, datasource: Datasource) -> MetadataSnapshot:
        connection = await self._connect(datasource)
        try:
            rows = await connection.fetch(
                """
                SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS name,
                       format_type(a.atttypid, a.atttypmod) AS data_type, a.attnum AS ordinal,
                       coalesce(col_description(c.oid, a.attnum), '') AS comment,
                       coalesce(obj_description(c.oid), '') AS table_comment,
                       EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid
                           AND i.indisprimary AND a.attnum=ANY(i.indkey)) AS is_pk
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                     JOIN pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname=$1 AND c.relkind IN ('r','v','m','p')
                     AND a.attnum>0 AND NOT a.attisdropped
                ORDER BY c.relname, a.attnum
            """,
                datasource.schema_name,
            )
            relations = await connection.fetch(
                """
                SELECT cl.relname AS from_table, fa.attname AS from_column,
                       cr.relname AS to_table, ta.attname AS to_column
                FROM pg_constraint fk JOIN pg_class cl ON fk.conrelid=cl.oid
                    JOIN pg_namespace ns ON cl.relnamespace=ns.oid
                    JOIN pg_class cr ON fk.confrelid=cr.oid
                    JOIN LATERAL unnest(fk.conkey, fk.confkey) keys(f, t) ON true
                    JOIN pg_attribute fa ON fa.attrelid=cl.oid AND fa.attnum=keys.f
                    JOIN pg_attribute ta ON ta.attrelid=cr.oid AND ta.attnum=keys.t
                WHERE fk.contype='f' AND ns.nspname=$1
            """,
                datasource.schema_name,
            )
            return _metadata(rows, relations)
        except Exception:
            raise DatasourceUnavailable() from None
        finally:
            await connection.close()

    async def test(self, datasource: Datasource) -> ConnectionTest:
        try:
            connection = await self._connect(datasource)
            try:
                writable = await connection.fetchval(
                    """
                    SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n
                    ON c.relnamespace=n.oid WHERE n.nspname=$1 AND c.relkind IN ('r','p')
                    AND (has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
                         OR has_schema_privilege(n.oid, 'CREATE')))
                    OR (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
                """,
                    datasource.schema_name,
                )
                return ConnectionTest(
                    available=True,
                    read_only_account=not writable,
                    warning="Account has write privileges" if writable else None,
                )
            finally:
                await connection.close()
        except Exception:
            return ConnectionTest(available=False, read_only_account=False)


class _MysqlSession:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def explain(self, sql: str) -> float:
        async with self.connection.cursor() as cursor:
            await cursor.execute("EXPLAIN FORMAT=JSON " + sql)
            row = await cursor.fetchone()
            plan = json.loads(row[0])
            cost = plan["query_block"].get("cost_info", {}).get("query_cost")
            # An unavailable estimate must not disable the cost limit silently.
            return float(cost) if cost is not None else float("inf")

    async def fetch(self, sql: str, max_rows: int) -> QueryBatch:
        cursor = await self.connection.cursor(aiomysql.SSDictCursor)
        await cursor.execute(sql)
        columns = [item[0] for item in cursor.description]
        rows = await cursor.fetchmany(max_rows)
        # SSDictCursor.close drains all unread rows. Close the connection instead.
        self.connection.close()
        return QueryBatch(columns=columns, rows=list(rows))


class MysqlConnector(Connector):
    async def _connect(self, ds: Datasource, timeout: float = 30) -> Any:
        return await aiomysql.connect(
            host=ds.host,
            port=ds.port,
            db=ds.database,
            user=ds.username,
            password=ds.password.get_secret_value(),
            connect_timeout=timeout,
            ssl=ssl.create_default_context() if ds.tls else None,
            autocommit=False,
        )

    @asynccontextmanager
    async def read_session(
        self, datasource: Datasource, timeout: float
    ) -> AsyncIterator[QuerySession]:
        connection = await self._connect(datasource, timeout)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SET SESSION MAX_EXECUTION_TIME = " + str(int(timeout * 1000)))
                await cursor.execute("SET TRANSACTION READ ONLY")
                await cursor.execute("START TRANSACTION")
            yield _MysqlSession(connection)
        finally:
            # Do not drain arbitrarily large unbuffered results on cancellation.
            connection.close()

    async def metadata(self, datasource: Datasource) -> MetadataSnapshot:
        connection = await self._connect(datasource)
        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT c.TABLE_SCHEMA AS schema_name, c.TABLE_NAME AS table_name,
                        c.COLUMN_NAME AS name, c.COLUMN_TYPE AS data_type,
                        c.ORDINAL_POSITION AS ordinal, c.COLUMN_COMMENT AS comment,
                        t.TABLE_COMMENT AS table_comment, c.COLUMN_KEY='PRI' AS is_pk
                    FROM information_schema.COLUMNS c JOIN information_schema.TABLES t
                    ON c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME
                    WHERE c.TABLE_SCHEMA=%s ORDER BY c.TABLE_NAME,c.ORDINAL_POSITION
                """,
                    (datasource.database,),
                )
                rows = await cursor.fetchall()
                await cursor.execute(
                    """
                    SELECT TABLE_NAME AS from_table, COLUMN_NAME AS from_column,
                        REFERENCED_TABLE_NAME AS to_table, REFERENCED_COLUMN_NAME AS to_column
                    FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s
                    AND REFERENCED_TABLE_NAME IS NOT NULL
                """,
                    (datasource.database,),
                )
                relations = await cursor.fetchall()
                return _metadata(rows, relations)
        except Exception:
            raise DatasourceUnavailable() from None
        finally:
            connection.close()

    async def test(self, datasource: Datasource) -> ConnectionTest:
        try:
            connection = await self._connect(datasource)
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute("SHOW GRANTS FOR CURRENT_USER")
                    grants = [str(row[0]).upper() for row in await cursor.fetchall()]
                    # Unknown roles or privilege forms conservatively require manual review.
                    readonly = all(
                        grant.startswith(("GRANT SELECT ", "GRANT USAGE "))
                        and "WITH GRANT OPTION" not in grant
                        for grant in grants
                    )
                    return ConnectionTest(
                        available=True,
                        read_only_account=readonly,
                        warning=None if readonly else "Review account privileges",
                    )
            finally:
                connection.close()
        except Exception:
            return ConnectionTest(available=False, read_only_account=False)


class _ClickHouseSession:
    def __init__(self, client: Any) -> None:
        self.client = client

    async def explain(self, sql: str) -> float:
        # ClickHouse has no planner cost comparable to PostgreSQL/MySQL; EXPLAIN ESTIMATE only
        # gives rows to read per table. The cost cap does not apply; the server-side
        # max_execution_time and the row cap bound the query instead.
        return 0.0

    async def fetch(self, sql: str, max_rows: int) -> QueryBatch:
        # Wrap rather than append: the guarded SQL may already end with its own LIMIT.
        result = await self.client.query(f"SELECT * FROM ({sql}) LIMIT {int(max_rows) + 1}")
        columns = list(result.column_names)
        rows = [dict(zip(columns, row, strict=True)) for row in result.result_rows]
        return QueryBatch(columns=columns, rows=rows)


class ClickHouseConnector(Connector):
    async def _connect(
        self, ds: Datasource, timeout: float = 30, *, restricted: bool = True
    ) -> Any:
        try:
            from clickhouse_connect import get_async_client
        except ImportError:
            raise DatasourceUnavailable() from None
        # readonly=2: only read queries, while the client may still set its own limits;
        # the SQL guard rejects any SETTINGS clause, so queries cannot change them.
        settings = (
            {"readonly": "2", "max_execution_time": max(1, int(timeout))} if restricted else {}
        )
        return await get_async_client(
            host=ds.host,
            port=ds.port,
            database=ds.database,
            username=ds.username,
            password=ds.password.get_secret_value(),
            secure=ds.tls,
            connect_timeout=min(int(timeout), 30),
            send_receive_timeout=int(timeout) + 5,
            settings=settings,
        )

    @asynccontextmanager
    async def read_session(
        self, datasource: Datasource, timeout: float
    ) -> AsyncIterator[QuerySession]:
        client = await self._connect(datasource, timeout)
        try:
            yield _ClickHouseSession(client)
        finally:
            await client.close()

    async def metadata(self, datasource: Datasource) -> MetadataSnapshot:
        client = await self._connect(datasource)
        try:
            # Query system.columns and system.tables for metadata
            rows_result = await client.query(
                """
                SELECT t.database AS schema_name, t.name AS table_name,
                       c.name, c.type AS data_type, c.position AS ordinal,
                       c.comment AS comment, t.comment AS table_comment,
                       0 AS is_pk
                FROM system.columns c
                JOIN system.tables t ON c.table = t.name AND c.database = t.database
                WHERE t.database = %(db)s
                ORDER BY t.name, c.position
                """,
                parameters={"db": datasource.database},
            )
            # Convert result to list of dicts matching expected schema
            rows = []
            for row in rows_result.result_rows:
                rows.append(
                    {
                        "schema_name": row[0],
                        "table_name": row[1],
                        "name": row[2],
                        "data_type": row[3],
                        "ordinal": row[4],
                        "comment": row[5] or "",
                        "table_comment": row[6] or "",
                        "is_pk": row[7],
                    }
                )
            # ClickHouse has no foreign keys
            relations: list[Any] = []
            return _metadata(rows, relations)
        except Exception:
            raise DatasourceUnavailable() from None
        finally:
            await client.close()

    async def test(self, datasource: Datasource) -> ConnectionTest:
        try:
            # Without client-side settings, getSetting reports the account profile itself.
            client = await self._connect(datasource, restricted=False)
            try:
                result = await client.query("SELECT getSetting('readonly')")
                readonly = bool(result.result_rows) and int(result.result_rows[0][0]) > 0
            finally:
                await client.close()
        except Exception:
            return ConnectionTest(available=False, read_only_account=False)
        return ConnectionTest(
            available=True,
            read_only_account=readonly,
            warning=None if readonly else "Account is not read-only",
        )


def _metadata(rows: Any, relations: Any) -> MetadataSnapshot:
    tables: dict[tuple[str, str], TableMetadata] = {}
    for row in rows:
        key = (row["schema_name"], row["table_name"])
        table = tables.setdefault(
            key,
            TableMetadata(
                schema_name=key[0],
                name=key[1],
                comment=row["table_comment"],
                columns=[],
            ),
        )
        table.columns.append(
            ColumnMetadata(
                **{key: row[key] for key in ("name", "data_type", "comment", "ordinal", "is_pk")}
            )
        )
    return MetadataSnapshot(
        tables=list(tables.values()), relations=[RelationMetadata(**dict(row)) for row in relations]
    )


class ConnectorRegistry:
    def __init__(self, connectors: dict[Dialect, Connector] | None = None) -> None:
        self.connectors = connectors or {
            Dialect.POSTGRES: PostgresConnector(),
            Dialect.MYSQL: MysqlConnector(),
            Dialect.KINGBASE: PostgresConnector(),
            Dialect.GAUSSDB: PostgresConnector(),
            Dialect.CLICKHOUSE: ClickHouseConnector(),
        }

    def get(self, dialect: Dialect) -> Connector:
        return self.connectors[dialect]


class DatasourceInspector:
    """Administrative connection/metadata access; deliberately has no SQL execution API."""

    def __init__(self, connectors: ConnectorRegistry | None = None) -> None:
        self._connectors = connectors or ConnectorRegistry(
            {
                Dialect.POSTGRES: _PostgresInspectionConnector(),
                Dialect.MYSQL: MysqlConnector(),
                Dialect.KINGBASE: _PostgresInspectionConnector(),
                Dialect.GAUSSDB: _PostgresInspectionConnector(),
                Dialect.CLICKHOUSE: ClickHouseConnector(),
            }
        )

    async def test(self, datasource: Datasource) -> ConnectionTest:
        try:
            return await self._connectors.get(datasource.dialect).test(datasource)
        except Exception:
            return ConnectionTest(available=False, read_only_account=False)

    async def metadata(self, datasource: Datasource) -> MetadataSnapshot:
        try:
            return await self._connectors.get(datasource.dialect).metadata(datasource)
        except Exception:
            raise DatasourceUnavailable() from None


class _PostgresInspectionConnector(PostgresConnector):
    async def test(self, datasource: Datasource) -> ConnectionTest:
        try:
            connection = await self._connect(datasource)
            try:
                writable = await connection.fetchval(
                    """
                    SELECT has_schema_privilege($1, 'CREATE')
                        OR has_database_privilege(current_database(), 'CREATE')
                        OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n
                            ON c.relnamespace=n.oid WHERE n.nspname=$1
                            AND c.relkind IN ('r','p','v','m','f')
                            AND has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE'))
                        OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user
                            AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls))
                    """,
                    datasource.schema_name,
                )
                return ConnectionTest(
                    available=True,
                    read_only_account=not writable,
                    warning="Account has write privileges" if writable else None,
                )
            finally:
                await connection.close()
        except Exception:
            return ConnectionTest(available=False, read_only_account=False)
