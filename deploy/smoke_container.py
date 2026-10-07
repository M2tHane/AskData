"""Run scripts.smoke's existing HTTP assertions against real Compose containers.

Only deployment addresses are translated: loopback API/model URLs become service names,
and the source connection points at pg-import. No application dependencies or assertions
are replaced. This entrypoint requires a new, disposable meta database.
"""

import asyncio
import json
import secrets
from typing import Any
from unittest.mock import patch

import asyncpg
import httpx
from pydantic import SecretStr

from app.core.importer import ImportSettings
from app.core.logging import LogEvent, configure_logging, logger
from scripts.create_admin import create_admin
from scripts.smoke import exercise


class ContainerSmokeSettings(ImportSettings):
    smoke_import_admin_password: SecretStr | None = None
    smoke_api_host: str = "api"
    smoke_model_host: str = "smoke-model"
    smoke_import_host: str = "pg-import"
    smoke_api_port: int = 8000
    smoke_model_port: int = 8001
    smoke_import_port: int = 5432


class ComposeTransport(httpx.AsyncBaseTransport):
    """Translate host-only test coordinates into the deployment's internal network."""

    def __init__(self, settings: ContainerSmokeSettings) -> None:
        self.settings = settings
        self.transport: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        settings = self.settings
        if request.url.host != "127.0.0.1" or request.url.port not in {
            settings.smoke_api_port,
            settings.smoke_model_port,
        }:
            raise RuntimeError("Unexpected smoke address")
        host = (
            settings.smoke_model_host
            if request.url.port == settings.smoke_model_port
            else settings.smoke_api_host
        )
        content = await request.aread()
        if request.method == "POST" and request.url.path in {"/api/datasources", "/api/models"}:
            data = json.loads(content)
            if request.url.path == "/api/datasources":
                data["host"], data["port"] = settings.smoke_import_host, settings.smoke_import_port
            else:
                data["base_url"] = (
                    f"http://{settings.smoke_model_host}:{settings.smoke_model_port}/v1"
                )
            content = json.dumps(data).encode()
        headers = dict(request.headers)
        headers.pop("content-length", None)
        headers.pop("host", None)
        mapped = httpx.Request(
            request.method,
            request.url.copy_with(host=host),
            headers=headers,
            content=content,
            extensions=request.extensions,
        )
        return await self.transport.handle_async_request(mapped)

    async def aclose(self) -> None:
        await self.transport.aclose()


async def verify() -> None:
    settings = ContainerSmokeSettings()
    if settings.smoke_import_admin_password is None:
        raise RuntimeError("ASKDATA_SMOKE_IMPORT_ADMIN_PASSWORD is required")
    suffix = secrets.token_hex(6)
    schema, reader = f"smoke_{suffix}", f"smoke_reader_{suffix}"
    reader_password, admin_password = secrets.token_hex(24), secrets.token_hex(24)
    connection = await asyncpg.connect(
        host=settings.smoke_import_host,
        port=settings.smoke_import_port,
        database="askdata_import",
        user="import_admin",
        password=settings.smoke_import_admin_password.get_secret_value(),
        timeout=15,
    )
    try:
        # Static disposable fixture setup, never accepts user/model SQL.
        await connection.execute(f"""
CREATE SCHEMA {schema};
CREATE TABLE {schema}.orders (id bigint PRIMARY KEY, amount bigint NOT NULL);
INSERT INTO {schema}.orders VALUES (1,10),(2,20);
CREATE ROLE {reader} LOGIN PASSWORD '{reader_password}';
GRANT CONNECT ON DATABASE askdata_import TO {reader};
GRANT USAGE ON SCHEMA {schema} TO {reader};
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {reader};
""")
        await create_admin("admin", SecretStr(admin_password))
        env = {
            "ASKDATA_SMOKE_API_PORT": str(settings.smoke_api_port),
            "ASKDATA_SMOKE_MODEL_PORT": str(settings.smoke_model_port),
            "ASKDATA_SMOKE_DATASOURCE_PORT": str(settings.smoke_import_port),
            "ASKDATA_SMOKE_SCHEMA": schema,
            "ASKDATA_SMOKE_READER": reader,
            "ASKDATA_SMOKE_READER_PASSWORD": reader_password,
            "ASKDATA_BOOTSTRAP_PASSWORD": admin_password,
        }
        client_type = httpx.AsyncClient

        def client_factory(**options: Any) -> httpx.AsyncClient:
            return client_type(**options, transport=ComposeTransport(settings))

        with patch("scripts.smoke.httpx.AsyncClient", side_effect=client_factory):
            await exercise(env)
        logger.info(LogEvent.QUERY, status="container_smoke_passed")
    finally:
        try:
            await connection.execute(f"""
DROP SCHEMA IF EXISTS {schema} CASCADE;
DROP OWNED BY {reader};
DROP ROLE {reader};
""")
        finally:
            await connection.close()


def main() -> None:
    configure_logging()
    try:
        asyncio.run(verify())
    except Exception:
        # Driver/validation errors can include credentials; never emit raw exceptions.
        logger.info(LogEvent.FAILURE, status="container_smoke_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
