import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.config import get_settings
from app.core.datasource import ConnectorRegistry, Datasource
from app.core.db import Database
from app.core.deps import CurrentUser, Role
from app.core.permission import PermissionService, TableGrant
from app.core.sql.dialect import Dialect
from app.core.sql.executor import SqlExecutor
from app.core.sql.guard import SqlGuard
from app.modules.datasource.models import DatasourceRecord
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.service import FeedbackService
from app.modules.metric.repository import MetricRepository
from app.modules.metric.service import MetricService
from app.modules.org.models import Team, User
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.service import PromotionService
from app.modules.querylog.repository import QueryLogRepository
from app.modules.querylog.service import QueryLogService
from app.modules.schema.models import MetaColumn, MetaTable
from app.modules.semantic.repository import SemanticTermRepository
from app.modules.semantic.schemas import TargetReference
from app.modules.semantic.service import SemanticService
from tests.fakes import FakeConnector, FakePermissions

ROOT = Path(__file__).resolve().parents[2]


class Outbox:
    def __init__(self):
        self.events = []

    async def mark_dirty(self, kind, ref_id, datasource_id, *, actor):
        self.events.append((kind, ref_id, datasource_id))


class Targets:
    async def resolve_target(self, datasource_id, kind, ref_id):
        return TargetReference(
            table="orders",
            column=None if kind == "table" else {1: "id", 2: "phone", 3: "secret"}[ref_id],
        )


class Datasources:
    async def resolve(self, datasource_id, actor):
        return Datasource(
            id=datasource_id,
            dialect=Dialect.POSTGRES,
            host="fake",
            port=5432,
            database="fake",
            username="readonly",
            password=SecretStr("fake"),
        )


class Evaluations:
    def __init__(self):
        self.cases = []

    async def create_from_feedback(self, snapshot, expected, *, actor):
        self.cases.append((snapshot, expected))
        return len(self.cases)


@pytest.fixture
async def b_database(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'group_b.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database(url)
    async with database.transaction() as session:
        session.add_all([Team(id=1, name="one"), Team(id=2, name="two")])
        await session.flush()
        session.add_all(
            [
                User(id=1, username="admin", password_hash="fake", role="admin"),
                User(id=2, username="leader", password_hash="fake", role="leader", team_id=1),
                User(id=3, username="member", password_hash="fake", role="member", team_id=1),
                User(id=4, username="other", password_hash="fake", role="member", team_id=2),
            ]
        )
        await session.flush()
        session.add(
            DatasourceRecord(
                id=1,
                name="fake",
                type="postgresql",
                host="fake",
                port=5432,
                database="fake",
                username="readonly",
                secret_enc="fake",
                dialect="postgres",
            )
        )
        await session.flush()
        session.add(
            MetaTable(
                id=1,
                datasource_id=1,
                schema_name="public",
                name="orders",
                synced_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add_all(
            [
                MetaColumn(id=n, table_id=1, name=name, data_type="TEXT", ordinal=n)
                for n, name in enumerate(["id", "phone", "secret"], 1)
            ]
        )
    yield database
    await database.close()
    get_settings.cache_clear()


@pytest.fixture
async def b_services(b_database):
    async with b_database.transaction() as session:
        provider = FakePermissions(
            [
                TableGrant(
                    name="orders",
                    all_columns={
                        "id": "BIGINT",
                        "amount": "DECIMAL",
                        "phone": "TEXT",
                        "secret": "TEXT",
                    },
                    team_visible=True,
                    member_visible=True,
                    team_columns={"id", "phone", "amount"},
                    member_columns={"id", "phone", "amount"},
                )
            ]
        )
        permissions = PermissionService(provider)
        connector = FakeConnector([{"id": 1}])
        audit = AuditService(DatabaseAuditStore(session))
        index = Outbox()
        datasources = Datasources()
        semantic = SemanticService(
            SemanticTermRepository(session), permissions, Targets(), audit, index
        )
        metric = MetricService(
            MetricRepository(session),
            permissions,
            datasources,
            SqlGuard(),
            SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector})),
            audit,
            index,
        )
        querylog = QueryLogService(QueryLogRepository(session), audit)
        evaluations = Evaluations()
        feedback = FeedbackService(
            FeedbackRepository(session),
            querylog,
            permissions,
            datasources,
            SqlGuard(),
            audit,
            index,
            evaluations,
        )
        yield SimpleNamespace(
            session=session,
            semantic=semantic,
            metric=metric,
            querylog=querylog,
            feedback=feedback,
            promotion=PromotionService(PromotionRepository(session), semantic, metric, audit),
            permissions=permissions,
            provider=provider,
            connector=connector,
            index=index,
            audit=audit,
            evaluations=evaluations,
        )


@pytest.fixture
def admin():
    return CurrentUser(id=1, role=Role.ADMIN)


@pytest.fixture
def leader():
    return CurrentUser(id=2, role=Role.LEADER, team_id=1)


@pytest.fixture
def outsider():
    return CurrentUser(id=4, role=Role.MEMBER, team_id=2)
