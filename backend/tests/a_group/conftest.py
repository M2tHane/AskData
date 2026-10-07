import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox
from app.core.datasource import (
    ConnectionTest,
    DatasourceInspector,
    MetadataSnapshot,
)
from app.core.db import Database
from app.core.deps import CurrentUser, Role
from app.modules.auth.service import build_service as build_auth
from app.modules.datasource.service import build_service as build_datasource
from app.modules.org.schemas import TeamWrite, UserWrite
from app.modules.org.service import build_service as build_org
from app.modules.permission.service import build_service as build_grants
from app.modules.schema.service import build_service as build_schema
from tests.fakes import MemoryScopeCache

ROOT = Path(__file__).resolve().parents[2]


class Inspector(DatasourceInspector):
    def __init__(self):
        self.result = ConnectionTest(available=True, read_only_account=True)
        self.snapshot = MetadataSnapshot(tables=[])

    async def test(self, datasource):
        return self.result

    async def metadata(self, datasource):
        return self.snapshot


@pytest.fixture
async def identity_db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    settings = Settings(
        database_url=SecretStr(url),
        jwt_secret=SecretStr("j" * 32),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "0003")
    database = Database(url)
    box = SecretBox(settings.encryption_key)
    cache, inspector = MemoryScopeCache(), Inspector()
    async with database.transaction() as session:
        org = build_org(session)
        admin = await org.bootstrap_admin("admin", SecretStr("administrator-password"))
        actor = CurrentUser(id=admin.id, role=Role.ADMIN)
        team = await org.create_team(TeamWrite(name="sales"), actor=actor)
        other = await org.create_team(TeamWrite(name="other"), actor=actor)
        leader = await org.create_user(
            UserWrite(
                username="leader",
                password=SecretStr("leader-password"),
                role=Role.LEADER,
                team_id=team.id,
            ),
            actor=actor,
        )
        member = await org.create_user(
            UserWrite(
                username="member",
                password=SecretStr("member-password"),
                role=Role.MEMBER,
                team_id=team.id,
            ),
            actor=actor,
        )

    class World:
        def __init__(self):
            self.database, self.settings, self.box = database, settings, box
            self.cache, self.inspector = cache, inspector
            self.admin = actor
            self.leader = CurrentUser(id=leader.id, role=Role.LEADER, team_id=team.id)
            self.member = CurrentUser(id=member.id, role=Role.MEMBER, team_id=team.id)
            self.team, self.other = team.id, other.id

        def org(self, session):
            return build_org(session)

        def auth(self, session):
            return build_auth(session, settings)

        def datasource(self, session):
            return build_datasource(session, self.box, self.inspector)

        def schema(self, session):
            return build_schema(session, self.box, self.inspector)

        def grants(self, session):
            return build_grants(session, box, cache)

    yield World()
    await database.close()
    get_settings.cache_clear()
