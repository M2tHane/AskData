"""Composition regression tests, deliberately within I3's scripts/ boundary."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from arq.connections import ArqRedis
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.composition import Composition, PendingUploadGrants, RuntimeSettings
from app.core.audit import AuditLog, QueryRun
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import AppError
from app.core.importer import ImportReadGateway, PostgresImportGateway
from app.core.permission import PermissionService, RedisScopeCache
from app.core.storage import StoredObject, object_key
from app.core.tasks import TaskContext, TaskRecord, TaskStatus
from app.main import create_app
from app.modules.auth.schemas import LoginRequest
from app.modules.chat.models import Conversation, Message
from app.modules.datasource.models import DatasourceRecord
from app.modules.feedback.models import Feedback
from app.modules.org.schemas import TeamWrite
from app.modules.upload.models import UploadJob
from tests.fakes import MemoryScopeCache, MemoryTaskStore

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


class MemoryRetentionStorage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.values[key] = data
        return StoredObject(key=key, size=len(data), sha256=sha256(data).hexdigest())

    async def get(self, key: str) -> bytes:
        return self.values[key]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


async def run_retention(runtime: Composition) -> None:
    store = MemoryTaskStore()
    record = TaskRecord(id=str(uuid4()), type="retention")
    await store.create(record)
    await store.transition(record.id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    await runtime.handlers()["retention"](TaskContext(record, store))


async def test_retention_task_purges_expired_chat_feedback_and_upload_rows(
    runtime: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryRetentionStorage()
    monkeypatch.setattr(runtime, "storage", storage)
    now = datetime.now(UTC)
    expired_at = now - timedelta(days=200)
    live_at = now + timedelta(days=30)
    async with runtime.services(transaction=True) as services:
        session = services.session
        for created_at in (expired_at, live_at):
            session.add(
                QueryRun(
                    payload={},
                    created_by=1,
                    created_at=created_at,
                    expires_at=live_at,
                )
            )
        await session.flush()
        runs = list(await session.scalars(select(QueryRun).order_by(QueryRun.id)))
        expired_run, live_run = runs
        session.add(
            DatasourceRecord(
                name="retention",
                type="postgresql",
                host="unused",
                port=5432,
                database="unused",
                username="unused",
                secret_enc="unused",
                dialect="postgres",
            )
        )
        conversation = Conversation(
            user_id=1, datasource_id=1, title="retention", created_at=live_at
        )
        session.add(conversation)
        await session.flush()
        session.add(
            Feedback(
                query_run_id=expired_run.id,
                query_run_created_at=expired_at,
                user_id=1,
                rating="down",
                comment="",
                snapshot={},
                created_at=expired_at,
                updated_at=expired_at,
                expires_at=expired_at,
            )
        )
        session.add(
            Feedback(
                query_run_id=live_run.id,
                query_run_created_at=live_at,
                user_id=1,
                rating="up",
                comment="",
                snapshot={},
                expires_at=live_at,
            )
        )
        session.add(
            Message(
                conversation_id=conversation.id,
                user_id=1,
                role="user",
                content="expired",
                created_at=expired_at,
                updated_at=expired_at,
                expires_at=expired_at,
            )
        )
        session.add(
            Message(
                conversation_id=conversation.id,
                user_id=1,
                role="assistant",
                content="live",
                expires_at=live_at,
            )
        )
        keys = {}
        for version, age in ((1, 200), (2, 10)):
            keys[version] = object_key(1, 7, version, "sales.csv")
            session.add(
                UploadJob(
                    datasource_id=7,
                    owner_team_id=1,
                    version=version,
                    filename="sales.csv",
                    file_key=keys[version],
                    sha256="0" * 64,
                    size=3,
                    parse_config={"table_name": "sales"},
                    mode="overwrite",
                    status="success",
                    row_count=1,
                    created_by=1,
                    created_at=now - timedelta(days=age),
                    updated_at=now - timedelta(days=age),
                )
            )
            await storage.put(keys[version], b"x\n1", "text/csv")

    # Without a scheduler admin the upload cleaner stays off but the rest still runs.
    await run_retention(runtime)
    async with runtime.services() as services:
        feedbacks = list(await services.session.scalars(select(Feedback)))
        assert [item.rating for item in feedbacks] == ["up"]
        messages = list(await services.session.scalars(select(Message)))
        assert [item.content for item in messages] == ["live"]
        assert len(list(await services.session.scalars(select(UploadJob)))) == 2
        assert storage.deleted == []

    runtime.settings = runtime.settings.model_copy(update={"scheduler_admin_id": 1})
    await run_retention(runtime)
    async with runtime.services() as services:
        jobs = list(await services.session.scalars(select(UploadJob)))
        assert [(item.datasource_id, item.version) for item in jobs] == [(7, 2)]
        assert storage.deleted == [keys[1]]
        audits = [
            record
            for record in await services.session.scalars(select(AuditLog))
            if record.payload["action"] == "upload.retention"
        ]
        assert len(audits) == 1 and audits[0].payload["actor_id"] == 1


async def test_retention_task_needs_a_live_admin_for_upload_history(
    runtime: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryRetentionStorage()
    monkeypatch.setattr(runtime, "storage", storage)
    runtime.settings = runtime.settings.model_copy(update={"scheduler_admin_id": 999})
    async with runtime.services(transaction=True) as services:
        services.session.add(
            UploadJob(
                datasource_id=7,
                owner_team_id=1,
                version=1,
                filename="sales.csv",
                file_key=object_key(1, 7, 1, "sales.csv"),
                sha256="0" * 64,
                size=3,
                parse_config={"table_name": "sales"},
                mode="overwrite",
                status="success",
                row_count=1,
                created_by=1,
                created_at=datetime.now(UTC) - timedelta(days=200),
                updated_at=datetime.now(UTC) - timedelta(days=200),
            )
        )
    await run_retention(runtime)
    async with runtime.services() as services:
        assert len(list(await services.session.scalars(select(UploadJob)))) == 1
        assert storage.deleted == []
