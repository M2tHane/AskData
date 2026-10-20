"""Regression checks for container address adaptation; no business assertions replaced."""

import json
from typing import Any

import httpx
import pytest
from deploy.smoke_container import ComposeTransport, ContainerSmokeSettings
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("path", "data", "expected"),
    [
        (
            "/api/auth/login",
            {"username": "admin", "password": "private-value"},
            {"username": "admin", "password": "private-value"},
        ),
        (
            "/api/datasources",
            {
                "host": "127.0.0.1",
                "port": 54322,
                "schema_name": "scope_a",
                "password": "private-value",
            },
            {
                "host": "pg-import",
                "port": 5432,
                "schema_name": "scope_a",
                "password": "private-value",
            },
        ),
        (
            "/api/models",
            {
                "base_url": "http://127.0.0.1:8001/v1",
                "model_name": "smoke",
                "api_key": "private-value",
            },
            {
                "base_url": "http://smoke-model:8001/v1",
                "model_name": "smoke",
                "api_key": "private-value",
            },
        ),
    ],
)
async def test_address_translation_preserves_credentials_and_scope(
    path: str,
    data: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    def observe(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api" and request.url.port == 8000
        assert request.url.path == path
        assert request.headers["authorization"] == "Bearer private-value"
        assert json.loads(request.content) == expected
        assert int(request.headers["content-length"]) == len(request.content)
        return httpx.Response(200, json={"code": "OK", "data": {"status": "success"}})

    transport = ComposeTransport(
        ContainerSmokeSettings(
            smoke_import_admin_password=SecretStr("private-value"),
        )
    )
    transport.transport = httpx.MockTransport(observe)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        result = await client.post(
            path, json=data, headers={"Authorization": "Bearer private-value"}
        )
        assert result.json() == {"code": "OK", "data": {"status": "success"}}


@pytest.mark.parametrize("url", ["http://outside.invalid:8000", "http://127.0.0.1:9999"])
async def test_unexpected_deployment_address_fails_before_connecting(url: str) -> None:
    transport = ComposeTransport(
        ContainerSmokeSettings(
            smoke_import_admin_password=SecretStr("private-value"),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="Unexpected smoke address"):
            await client.get(url)


async def test_model_health_uses_independent_model_container() -> None:
    def observe(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://smoke-model:8001/healthz")
        return httpx.Response(200)

    transport = ComposeTransport(
        ContainerSmokeSettings(
            smoke_import_admin_password=SecretStr("private-value"),
        )
    )
    transport.transport = httpx.MockTransport(observe)
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get("http://127.0.0.1:8001/healthz")).status_code == 200
