"""Composition regression tests, deliberately within I3's scripts/ boundary."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from arq.connections import ArqRedis
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.composition import Composition, PendingUploadGrants, RuntimeSettings
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.importer import ImportReadGateway, PostgresImportGateway
from app.core.permission import PermissionService, RedisScopeCache
from app.main import create_app
from app.modules.auth.schemas import LoginRequest
from app.modules.org.schemas import TeamWrite
from tests.fakes import MemoryScopeCache

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Composition]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'composition.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    settings = RuntimeSettings(
        database_url=SecretStr(url),
        jwt_secret=SecretStr("j" * 32),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    database = Database(url)
    composition = Composition(settings, database, cast(ArqRedis, object()))
    composition.cache = cast(RedisScopeCache, MemoryScopeCache())
    async with composition.services(transaction=True) as services:
        await services.org.bootstrap_admin("admin", SecretStr("composition-password"))
    yield composition
    await database.close()
    get_settings.cache_clear()


async def test_concurrent_requests_have_distinct_sessions_and_usage(runtime: Composition) -> None:
    async with runtime.services() as first, runtime.services() as second:
        assert first.session is not second.session
        assert first.usage is not second.usage
        pair = await first.auth.login(
            LoginRequest(
                username="admin",
                password=SecretStr("composition-password"),
            )
        )
        await first.session.commit()
        actor = await second.auth.resolve(pair.access_token)
        assert actor.id == 1 and actor.role == "admin"


async def test_pending_upload_is_empty_scope_without_bypassing_permissions(
    runtime: Composition,
) -> None:
    async with runtime.services(transaction=True) as services:
        actor = await services.org.identity(1)
        team = await services.org.create_team(TeamWrite(name="upload"), actor=actor)
        identifier = await services.datasource.reserve_upload("pending", team.id, actor=actor)
        permissions = PermissionService(PendingUploadGrants(services))
        scope = await permissions.visible_scope(actor, identifier)
        assert scope.tables == {}
        with pytest.raises(AppError):
            await permissions.visible_scope(actor, identifier + 1)


async def test_eval_crud_works_without_offline_identity_bindings(runtime: Composition) -> None:
    async with runtime.services() as services:
        actor = await services.org.identity(1)
        await services.session.rollback()
        evaluations = services.evaluation()
        assert await evaluations.load_cases(actor=actor) == 100
        with pytest.raises(AppError):
            await runtime.mapped_assets()


async def test_api_and_worker_importer_credentials_are_separate(runtime: Composition) -> None:
    settings = RuntimeSettings(
        **runtime.settings.model_dump(exclude={"import_writer_url", "import_reader_password"}),
        import_reader_password=SecretStr("read-password"),
        import_writer_url=SecretStr("postgresql://writer:write-password@localhost:5432/import_db"),
    )
    api = Composition(settings, runtime.database, cast(ArqRedis, object()))
    worker = Composition(settings, runtime.database, cast(ArqRedis, object()), worker=True)
    assert isinstance(api.importer, ImportReadGateway)
    assert isinstance(worker.importer, PostgresImportGateway)
    assert api.importer.query_datasource(7).username == "ds_7_reader"
    assert worker.importer.query_datasource(7).read_only


def test_health_and_auth_remain_available_without_database() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        assert client.get("/healthz").json()["data"] == {"status": "ok"}
        assert client.get("/api/org/users").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={
                    "username": "admin",
                    "password": "unconfigured-password",
                },
            ).status_code
            == 503
        )


def test_all_business_routes_are_mounted_under_api_prefix() -> None:
    paths = create_app(Settings(database_url=None)).openapi()["paths"]
    assert all(path == "/healthz" or path.startswith("/api/") for path in paths)
    assert {"/api/eval/runs", "/api/upload/preview", "/api/embedding/rebuild"} <= paths.keys()


async def test_task_registry_contains_all_business_jobs(runtime: Composition) -> None:
    assert set(runtime.handlers()) == {
        "schema_sync",
        "upload.import",
        "eval.run",
        "embedding.update",
        "embedding.rebuild",
        "retention",
    }
