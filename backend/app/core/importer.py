"""Worker-only, typed file import gateway; never accepts user/model SQL."""

import re
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, Literal, Protocol

import asyncpg
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.core.datasource import Datasource
from app.core.errors import AppError, DatasourceUnavailable, ErrorCode

ColumnType = Literal["text", "integer", "number", "boolean", "date", "datetime"]
SQL_TYPES = {
    "text": "TEXT",
    "integer": "BIGINT",
    "number": "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}


class ImportSettings(Settings):
    import_writer_url: SecretStr | None = None
    import_reader_password: SecretStr | None = None
    upload_max_bytes: int = 50 * 1024 * 1024


class ImportColumn(BaseModel):
    name: str
    data_type: ColumnType = "text"
    description: str = ""


class ImportPlan(BaseModel):
    datasource_id: int
    table_name: str
    columns: list[ImportColumn]
    mode: Literal["overwrite", "append"] = "overwrite"
    batch_size: int = 1000

    @property
    def schema_name(self) -> str:
        return f"ds_{self.datasource_id}"


class ImportTransaction(Protocol):
    async def write(self, rows: Iterable[tuple[Any, ...]]) -> int: ...


class ImportGateway(Protocol):
    def transaction(self, plan: ImportPlan) -> Any:
        """Single PG transaction: prepare, COPY batches, register callback, commit/rollback."""
        ...

    def query_datasource(self, datasource_id: int) -> Datasource: ...


class ImportReadGateway:
    """API-side connection template: contains only reader credentials, cannot write."""

    def __init__(self, template: Datasource) -> None:
        if not template.read_only:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        self.template = template

    def query_datasource(self, datasource_id: int) -> Datasource:
        if datasource_id < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        return self.template.model_copy(
            update={
                "id": datasource_id,
                "username": f"ds_{datasource_id}_reader",
                "schema_name": f"ds_{datasource_id}",
            }
        )

    def transaction(self, plan: ImportPlan) -> Any:
        raise AppError(ErrorCode.FORBIDDEN, http_status=403)


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    return '"' + value + '"'


class _CopyTransaction:
    def __init__(self, connection: Any, plan: ImportPlan) -> None:
        self.connection, self.plan = connection, plan

    async def write(self, rows: Iterable[tuple[Any, ...]]) -> int:
        count, batch = 0, []
        for row in rows:
            if len(row) != len(self.plan.columns):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            batch.append(row)
            if len(batch) >= self.plan.batch_size:
                await self._copy(batch)
                count += len(batch)
                batch = []
        if batch:
            await self._copy(batch)
            count += len(batch)
        return count

    async def _copy(self, batch: list[tuple[Any, ...]]) -> None:
        await self.connection.copy_records_to_table(
            self.plan.table_name,
            schema_name=self.plan.schema_name,
            columns=[column.name for column in self.plan.columns],
            records=batch,
        )


class PostgresImportGateway:
    def __init__(
        self,
        writer: Datasource,
        reader_password: SecretStr,
        connect: Callable[..., Any] = asyncpg.connect,
    ) -> None:
        if writer.read_only:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        self.writer, self.reader_password, self.connect = writer, reader_password, connect

    def query_datasource(self, datasource_id: int) -> Datasource:
        return self.writer.model_copy(
            update={
                "id": datasource_id,
                "username": f"ds_{datasource_id}_reader",
                "password": self.reader_password,
                "schema_name": f"ds_{datasource_id}",
                "read_only": True,
            }
        )

    @asynccontextmanager
    async def transaction(self, plan: ImportPlan) -> AsyncIterator[ImportTransaction]:
        if (
            plan.datasource_id < 1
            or not plan.columns
            or not 1 <= plan.batch_size <= 10000
            or len({c.name for c in plan.columns}) != len(plan.columns)
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        schema, table = _identifier(plan.schema_name), _identifier(plan.table_name)
        role = _identifier(self.query_datasource(plan.datasource_id).username)
        definitions = ", ".join(
            f"{_identifier(c.name)} {SQL_TYPES[c.data_type]}" for c in plan.columns
        )
        connection = None
        try:
            connection = await self.connect(
                host=self.writer.host,
                port=self.writer.port,
                database=self.writer.database,
                user=self.writer.username,
                password=self.writer.password.get_secret_value(),
                ssl=True if self.writer.tls else False,
                timeout=30,
                command_timeout=300,
            )
            async with connection.transaction():
                # Also serializes concurrent imports to different tables of the same source.
                await connection.execute("SELECT pg_advisory_xact_lock($1)", plan.datasource_id)
                await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                existing = await connection.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position",
                    plan.schema_name,
                    plan.table_name,
                )
                expected = [(c.name, SQL_TYPES[c.data_type].lower()) for c in plan.columns]
                # PG reports TIMESTAMP as timestamp without time zone.
                expected = [
                    (n, "timestamp without time zone" if t == "timestamp" else t)
                    for n, t in expected
                ]
                if plan.mode == "append":
                    if [(r["column_name"], r["data_type"]) for r in existing] != expected:
                        raise AppError(ErrorCode.CONFLICT, "Append column structure differs")
                else:
                    await connection.execute(f"DROP TABLE IF EXISTS {schema}.{table}")
                    await connection.execute(f"CREATE TABLE {schema}.{table} ({definitions})")
                # A distinct NOINHERIT reader per schema cannot read another source's schema.
                role_exists = await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)",
                    self.query_datasource(plan.datasource_id).username,
                )
                if not role_exists:
                    password = self.reader_password.get_secret_value().replace("'", "''")
                    await connection.execute(
                        f"CREATE ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                        f"NOCREATEROLE NOREPLICATION PASSWORD '{password}'"
                    )
                await connection.execute(
                    f"GRANT CONNECT ON DATABASE {_identifier(self.writer.database)} TO {role}"
                )
                await connection.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
                await connection.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
                await connection.execute(f"GRANT SELECT ON {schema}.{table} TO {role}")
                yield _CopyTransaction(connection, plan)
        except AppError:
            raise
        except Exception:
            raise DatasourceUnavailable() from None
        finally:
            if connection is not None:
                await connection.close()
