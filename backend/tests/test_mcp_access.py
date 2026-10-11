"""MCP connections on a real (SQLite, migrated) database: service accounts, keys, the
column allowlist, daily limits, conversation ownership and inherited definitions."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import SecretBox
from app.core.db import Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.masking import MaskKind, MaskRule
from app.core.response import PageParams
from app.modules.auth.service import audit_for
from app.modules.datasource.models import DatasourceRecord
from app.modules.datasource.service import build_service as build_datasource
from app.modules.mcpaccess.models import McpClient
from app.modules.mcpaccess.schemas import McpClientWrite, OverrideRestore, OverrideWrite
from app.modules.mcpaccess.service import McpAccessService, build_service
from app.modules.metric.models import Metric
from app.modules.metric.service import QueryDatasources
from app.modules.metric.service import build_service as build_metric
from app.modules.org.models import Team, User
from app.modules.org.service import build_service as build_org
from app.modules.permission.schemas import ClientColumnWrite, ClientGrantsWrite
from app.modules.permission.service import build_service as build_grants
from app.modules.schema.models import MetaColumn, MetaTable
from app.modules.schema.service import build_service as build_schema
from app.modules.semantic.models import SemanticTerm
from app.modules.semantic.service import SchemaTargets
from app.modules.semantic.service import build_service as build_semantic

ROOT = Path(__file__).resolve().parents[1]
ADMIN = CurrentUser(id=1, role=Role.ADMIN)
BOX = SecretBox(SecretStr(Fernet.generate_key().decode()))
COLUMNS = {11: "id", 12: "amount", 13: "salary", 14: "phone"}


@pytest.fixture
async def database(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    db = Database(url)
    async with db.transaction() as session:
        session.add(Team(id=1, name="sales"))
        await session.flush()
        session.add_all(
            [
                User(id=1, username="admin", password_hash="x", role="admin"),
                User(id=2, username="member", password_hash="x", role="member", team_id=1),
            ]
        )
        session.add(
            DatasourceRecord(
                id=1,
                name="shop",
                type="postgresql",
                host="fake",
                port=5432,
                database="shop",
                username="readonly",
                secret_enc=BOX.encrypt(SecretStr("fake")),
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
                MetaColumn(id=key, table_id=1, name=name, data_type="numeric", ordinal=key)
                for key, name in COLUMNS.items()
            ]
        )
        await session.flush()
        session.add_all(
            [
                SemanticTerm(
                    id=1,
                    datasource_id=1,
                    scope="platform",
                    status="active",
                    aliases=["实付金额"],
                    description="订单实付",
                    target_type="column",
                    target_id=12,
                    target_column_id=12,
                ),
                SemanticTerm(
                    id=2,
                    datasource_id=1,
                    scope="platform",
                    status="active",
                    aliases=["工资"],
                    target_type="column",
                    target_id=13,
                    target_column_id=13,
                ),
                Metric(
                    id=1,
                    datasource_id=1,
                    scope="platform",
                    status="active",
                    name="销售额",
                    aliases=[],
                    description="实付合计",
                    sql_template="SELECT SUM(amount) AS v FROM public.orders",
                    params={},
                    last_validated_at=datetime.now(UTC),
                ),
            ]
        )
    yield db
    await db.close()
    get_settings.cache_clear()


@asynccontextmanager
async def access(database, transaction=True):
    # The agent path runs on a plain session and commits its own steps (transaction=False).
    async with database.transaction() if transaction else database.session() as session:
        org = build_org(session)
        grants = build_grants(session, BOX)
        datasource = build_datasource(session, BOX)
        schema = build_schema(session, BOX)
        semantic = build_semantic(session, grants.permissions, SchemaTargets(schema))
        metric = build_metric(
            session, grants.permissions, QueryDatasources(datasource, grants.permissions), None
        )
        yield build_service(session, org, grants, datasource, semantic, metric, audit_for(session))


async def _create(database, **settings) -> tuple[int, str]:
    async with access(database) as service:
        client, key = await service.create(
            McpClientWrite(name="销售分析 Agent", **settings), actor=ADMIN
        )
    return client.id, key.key


async def _grant(database, client_id, columns, masks=None):
    async with access(database) as service:
        await service.replace_grants(
            client_id,
            1,
            ClientGrantsWrite(
                columns=[
                    ClientColumnWrite(column_id=c, mask_rule=(masks or {}).get(c)) for c in columns
                ]
            ),
            actor=ADMIN,
        )


async def test_a_connection_is_a_service_account_that_cannot_log_in(database):
    client_id, key = await _create(database)
    assert key.startswith("ak_")
    async with access(database) as service:
        row = await service.repository.clients.get(client_id)
        with pytest.raises(AppError):
            await service.org.identity(row.user_id)
        with pytest.raises(AppError):
            await service.org.authenticate(f"mcp-{row.public_id}", SecretStr("!service"))
        users = await service.org.list_users(PageParams(), actor=ADMIN)
        assert row.user_id not in {user.id for user in users.items}
        stored = await service.repository.keys(client_id)
        assert key not in {k.key_hash for k in stored} and stored[0].prefix == key[:10]


async def test_keys_rotate_revoke_and_follow_the_connection(database):
    client_id, key = await _create(database)
    async with access(database) as service:
        assert (await service.authenticate(key)).client_id == client_id
        with pytest.raises(AppError) as wrong:
            await service.authenticate("ak_" + "x" * 40)
        assert wrong.value.http_status == 401
        graced = await service.rotate_key(client_id, 2, actor=ADMIN)
    async with access(database) as service:
        # Both work during the grace period.
        await service.authenticate(key)
        await service.authenticate(graced.key)
        fresh = await service.rotate_key(client_id, 0, actor=ADMIN)
    async with access(database) as service:
        for old in (key, graced.key):
            with pytest.raises(AppError):
                await service.authenticate(old)
        await service.authenticate(fresh.key)
        await service.set_status(client_id, False, actor=ADMIN)
    async with access(database) as service:
        with pytest.raises(AppError) as disabled:
            await service.authenticate(fresh.key)
        assert disabled.value.http_status == 403
        await service.set_status(client_id, True, actor=ADMIN)
        row = await service.repository.clients.get(client_id)
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    async with access(database) as service:
        with pytest.raises(AppError):
            await service.authenticate(fresh.key)


async def test_only_admins_manage_connections(database):
    member = CurrentUser(id=2, role=Role.MEMBER, team_id=1)
    async with access(database) as service:
        with pytest.raises(AppError):
            await service.create(McpClientWrite(name="x"), actor=member)
        with pytest.raises(AppError):
            await service.clients(actor=member)


async def test_scope_is_exactly_the_allowlist(database):
    client_id, _ = await _create(database)
    async with access(database) as service:
        user = service.principal_user(await service.repository.clients.get(client_id))
        assert (await service.grants.permissions.visible_scope(user, 1)).tables == {}
    await _grant(database, client_id, [11, 12, 14], {14: MaskRule(kind=MaskKind.PHONE)})
    async with access(database) as service:
        scope = await service.grants.permissions.visible_scope(user, 1)
        table = scope.tables["public.orders"]
        assert table.columns == {"id", "amount", "phone"}
        assert set(table.masks) == {"phone"}
        # A column outside the datasource's catalog cannot be granted.
        with pytest.raises(AppError):
            await service.replace_grants(
                client_id,
                1,
                ClientGrantsWrite(columns=[ClientColumnWrite(column_id=999)]),
                actor=ADMIN,
            )
    async with database.transaction() as session:
        session.add(MetaColumn(id=15, table_id=1, name="bonus", data_type="numeric", ordinal=15))
    async with access(database) as service:
        # A column a later sync brings in stays hidden until granted.
        scope = await service.grants.permissions.visible_scope(user, 1)
        assert "bonus" not in scope.tables["public.orders"].columns


async def test_daily_limit_counts_calls_and_resets_each_day(database):
    client_id, key = await _create(database, daily_call_limit=2)
    async with access(database, transaction=False) as service:
        principal = await service.authenticate(key)
        await service.admit(principal)
        await service.admit(principal)
        with pytest.raises(AppError) as exhausted:
            await service.admit(principal)
        assert exhausted.value.code == ErrorCode.QUOTA_EXCEEDED
    async with database.transaction() as session:
        row = await session.scalar(select(McpClient).where(McpClient.id == client_id))
        row.daily_date = datetime.now(UTC).date() - timedelta(days=1)
    async with access(database, transaction=False) as service:
        await service.admit(principal)
        assert (await service.get(client_id, actor=ADMIN)).calls_today == 1


async def test_conversations_belong_to_one_connection_and_expire(database):
    alpha, alpha_key = await _create(database)
    beta, beta_key = await _create(database)
    async with database.transaction() as session:
        from app.modules.chat.models import Conversation

        session.add(Conversation(id=7, user_id=1, datasource_id=1, title="t"))
    async with access(database) as service:
        a = await service.authenticate(alpha_key)
        b = await service.authenticate(beta_key)
        binding = await service.bind_conversation(a, 7, 1)
        public_id = binding.public_id
    async with access(database) as service:
        assert (await service.conversation(a, public_id)).conversation_id == 7
        with pytest.raises(AppError) as other:
            await service.conversation(b, public_id)
        with pytest.raises(AppError) as unknown:
            await service.conversation(a, "conv_nope")
        # Another connection's id and an unknown id look the same.
        assert other.value.code == unknown.value.code == ErrorCode.NOT_FOUND
        assert other.value.message == unknown.value.message
        row = await service.repository.conversation(alpha, public_id)
        row.last_used_at = datetime.now(UTC) - timedelta(days=8)
    async with access(database) as service:
        with pytest.raises(AppError):
            await service.conversation(a, public_id)


async def test_definitions_are_inherited_edited_hidden_and_restored(database):
    client_id, key = await _create(database)
    await _grant(database, client_id, [11, 12])
    async with access(database) as service:
        items = await service.overlay_items(client_id, 1, actor=ADMIN)
        # 工资 reads a column this connection is not granted: not inherited at all.
        assert {(i.kind, i.name, i.state) for i in items} == {
            ("semantic", "实付金额", "default"),
            ("metric", "销售额", "default"),
        }
        edited = await service.set_override(
            client_id,
            1,
            OverrideWrite(
                kind="semantic", item_id=1, action="edit", payload={"description": "含运费"}
            ),
            actor=ADMIN,
        )
        assert edited.state == "edited" and edited.effective["description"] == "含运费"
        assert edited.platform["description"] == "订单实付"
        await service.set_override(
            client_id, 1, OverrideWrite(kind="metric", item_id=1, action="hide"), actor=ADMIN
        )
        user = service.principal_user(await service.repository.clients.get(client_id))
        terms = await service.semantics(user, 1, await service.semantic.list(user, 1))
        assert [t.description for t in terms] == ["含运费"]
        assert await service.metrics(user, 1, await service.metric.list(user, 1)) == []
        # Platform users are unaffected.
        assert (await service.semantic.list(ADMIN, 1))[0].description == "订单实付"
    async with access(database) as service:
        # A metric edit must stay inside the connection's grants.
        with pytest.raises(AppError) as outside:
            await service.set_override(
                client_id,
                1,
                OverrideWrite(
                    kind="metric",
                    item_id=1,
                    action="edit",
                    payload={"sql_template": "SELECT SUM(salary) AS v FROM public.orders"},
                ),
                actor=ADMIN,
            )
        assert outside.value.code == ErrorCode.SQL_OUT_OF_SCOPE
        assert await service.restore(client_id, OverrideRestore(), actor=ADMIN) == 2
        assert {i.state for i in await service.overlay_items(client_id, 1, actor=ADMIN)} == {
            "default"
        }


async def test_deleting_a_connection_keeps_its_history_but_nothing_works(database):
    client_id, key = await _create(database)
    await _grant(database, client_id, [11, 12])
    async with access(database) as service:
        await service.delete(client_id, actor=ADMIN)
    async with access(database) as service:
        with pytest.raises(AppError):
            await service.authenticate(key)
        assert await service.clients(actor=ADMIN) == []
        assert await service.grants.client_column_ids(client_id) == []
        row = await service.repository.session.scalar(
            select(McpClient).where(McpClient.id == client_id)
        )
        user = await service.repository.session.get(User, row.user_id)
        # Logs keep their subject: the rows stay, disabled.
        assert row.deleted_at is not None and user.status == "disabled"


def test_service_is_constructible_without_database():
    assert McpAccessService.principal_user(
        McpClient(id=3, user_id=9, public_id="mc_x", name="n")
    ) == CurrentUser(id=9, role=Role.MEMBER, client_id=3)


@pytest.mark.external
async def test_concurrent_calls_never_exceed_the_daily_limit_on_postgres(monkeypatch):
    import asyncpg

    from tests.test_external_services import container, ready

    password = secrets.token_urlsafe(24)
    with container(
        "pgvector/pgvector:pg16", [5432], {"POSTGRES_PASSWORD": password, "POSTGRES_DB": "askdata"}
    ) as ports:
        dsn = f"postgres:{password}@127.0.0.1:{ports[5432]}/askdata"
        await (await ready(lambda: asyncpg.connect(f"postgresql://{dsn}"))).close()
        url = f"postgresql+asyncpg://{dsn}"
        monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
        get_settings.cache_clear()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        await asyncio.to_thread(command.upgrade, config, "head")
        database = Database(url)
        try:
            async with database.transaction() as session:
                session.add(User(username="admin", password_hash="x", role="admin"))
            client_id, key = await _create(database, daily_call_limit=5)
            async with access(database, transaction=False) as service:
                principal = await service.authenticate(key)

            async def call():
                # Each call has its own session, as concurrent MCP requests do.
                async with access(database, transaction=False) as service:
                    try:
                        await service.admit(principal)
                        return True
                    except AppError:
                        return False

            assert sum(await asyncio.gather(*(call() for _ in range(20)))) == 5
            async with database.transaction() as session:
                row = await session.get(McpClient, client_id)
                assert row.daily_calls == 5
                # Yesterday's exhausted count: the first calls of a new day race to reset it.
                row.daily_date = datetime.now(UTC).date() - timedelta(days=1)
            assert sum(await asyncio.gather(*(call() for _ in range(20)))) == 5
            async with database.transaction() as session:
                assert (await session.get(McpClient, client_id)).daily_calls == 5
        finally:
            await database.close()
            get_settings.cache_clear()


async def _context(database, user, question="销售额 实付金额"):
    """What retrieval hands the model for this user: the bundle a prompt is built from."""
    from types import SimpleNamespace

    from app.modules.chat.pipeline import ContextBuilder
    from app.modules.chat.retrieval import ServiceRetrievalIndex

    async with access(database) as service:

        async def no_examples(user, datasource_id):
            return []

        index = ServiceRetrievalIndex(
            service.grants.schema,
            service.semantic,
            service.metric,
            SimpleNamespace(examples=no_examples),
            overlay=service,
        )
        scope = await service.grants.permissions.visible_scope(user, 1)
        bundle = await ContextBuilder(index, threshold=0).build(user, scope, question, full=True)
    return {(item.kind, item.name): item for item in bundle.items}, bundle


async def test_connection_overrides_reach_the_model_and_only_for_that_connection(database):
    client_id, _ = await _create(database)
    other_id, _ = await _create(database)
    await _grant(database, client_id, [11, 12])
    await _grant(database, other_id, [11, 12])
    async with access(database) as service:
        await service.set_override(
            client_id,
            1,
            OverrideWrite(
                kind="semantic", item_id=1, action="edit", payload={"description": "含运费"}
            ),
            actor=ADMIN,
        )
        await service.set_override(
            client_id, 1, OverrideWrite(kind="metric", item_id=1, action="hide"), actor=ADMIN
        )
        user = service.principal_user(await service.repository.clients.get(client_id))
        other = service.principal_user(await service.repository.clients.get(other_id))
    items, bundle = await _context(database, user)
    assert ("metric", "销售额") not in items
    assert items[("semantic", "实付金额")].description == "含运费"
    assert "salary" not in str(bundle.model_dump())
    # Another connection and the platform's own users keep the platform definitions.
    for someone in (other, ADMIN):
        theirs, _ = await _context(database, someone)
        assert ("metric", "销售额") in theirs
        assert theirs[("semantic", "实付金额")].description == "订单实付"


async def test_an_override_sleeps_while_its_column_is_revoked_and_wakes_with_it(database):
    client_id, _ = await _create(database)
    await _grant(database, client_id, [11, 12])
    async with access(database) as service:
        await service.set_override(
            client_id,
            1,
            OverrideWrite(
                kind="semantic", item_id=1, action="edit", payload={"description": "含运费"}
            ),
            actor=ADMIN,
        )
        user = service.principal_user(await service.repository.clients.get(client_id))
    await _grant(database, client_id, [11])
    items, _ = await _context(database, user)
    assert ("semantic", "实付金额") not in items
    async with access(database) as service:
        assert not [
            i
            for i in await service.overlay_items(client_id, 1, actor=ADMIN)
            if i.item_id == 1 and i.kind == "semantic"
        ]
    await _grant(database, client_id, [11, 12])
    items, _ = await _context(database, user)
    assert items[("semantic", "实付金额")].description == "含运费"
    async with access(database) as service:
        restored = [
            i
            for i in await service.overlay_items(client_id, 1, actor=ADMIN)
            if i.kind == "semantic" and i.item_id == 1
        ]
        assert restored[0].state == "edited"
