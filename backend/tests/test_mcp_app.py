"""The real application (create_app with its lifespan) on a migrated SQLite database: the admin
API creates and grants a connection, and an MCP SDK client uses its key against /mcp. Only Redis
is replaced, by in-memory fakes; everything between HTTP and the database is the shipped code."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox
from app.core.db import Database
from app.core.deps import CurrentUser, Role
from app.main import create_app
from app.modules.datasource.models import DatasourceRecord
from app.modules.metric.models import Metric
from app.modules.org.schemas import TeamWrite, UserWrite
from app.modules.org.service import build_service as build_org
from app.modules.schema.models import MetaColumn, MetaTable
from app.modules.semantic.models import SemanticTerm
from tests.fakes import MemoryScopeCache

ROOT = Path(__file__).resolve().parents[1]
PASSWORDS = {"admin": "administrator-password", "leader": "leader-password", "member": "m" * 16}
COLUMNS = {11: "id", 12: "amount", 13: "salary", 14: "phone"}
HOST = "http://localhost:8000"
# Names an agent granted only orders.id and orders.amount must never see.
HIDDEN = ("salary", "phone", "salaries", "员工底薪", "客户手机号", "人力成本", "hr")
EVAL = ROOT.parent / "eval" / "cases.jsonl"
UNSAFE = [
    case["question"]
    for case in map(json.loads, EVAL.read_text(encoding="utf-8").splitlines())
    if case["category"] in {"out_of_scope", "violation", "injection"}
]


def term(identifier, alias, column_id):
    return SemanticTerm(
        id=identifier,
        datasource_id=1,
        scope="platform",
        status="active",
        aliases=[alias],
        description=f"{alias}说明",
        target_type="column",
        target_id=column_id,
        target_column_id=column_id,
    )


def metric(identifier, name, sql):
    return Metric(
        id=identifier,
        datasource_id=1,
        scope="platform",
        status="active",
        name=name,
        aliases=[],
        description=f"{name}说明",
        sql_template=sql,
        params={},
        last_validated_at=datetime.now(UTC),
    )


@pytest.fixture
async def settings(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
    get_settings.cache_clear()
    settings = Settings(
        database_url=SecretStr(url),
        jwt_secret=SecretStr("j" * 32),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database(url)
    async with database.transaction() as session:
        org = build_org(session)
        admin = await org.bootstrap_admin("admin", SecretStr(PASSWORDS["admin"]))
        actor = CurrentUser(id=admin.id, role=Role.ADMIN)
        team = await org.create_team(TeamWrite(name="sales"), actor=actor)
        for name, role in (("leader", Role.LEADER), ("member", Role.MEMBER)):
            await org.create_user(
                UserWrite(
                    username=name, password=SecretStr(PASSWORDS[name]), role=role, team_id=team.id
                ),
                actor=actor,
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
                secret_enc=SecretBox(settings.encryption_key).encrypt(SecretStr("fake")),
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
                comment="订单",
                synced_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add_all(
            MetaColumn(id=key, table_id=1, name=name, data_type="numeric", ordinal=key)
            for key, name in COLUMNS.items()
        )
        await session.flush()
        # One definition of each kind on a granted column and one on an ungranted column.
        session.add_all(
            [
                term(1, "实付金额", 12),
                term(2, "员工底薪", 13),
                term(3, "客户手机号", 14),
                metric(1, "销售额", "SELECT SUM(amount) AS v FROM public.orders"),
                metric(2, "人力成本", "SELECT SUM(salary) AS v FROM public.orders"),
            ]
        )
    await database.close()
    yield settings
    get_settings.cache_clear()


async def add_ungranted(settings, name):
    """Adds a table on datasource 1 and a second datasource; neither is granted."""
    database = Database(settings.database_url.get_secret_value())
    async with database.transaction() as session:
        session.add(
            MetaTable(
                id=2,
                datasource_id=1,
                schema_name="public",
                name=name,
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            DatasourceRecord(
                id=2,
                name="hr",
                type="postgresql",
                host="fake",
                port=5432,
                database="hr",
                username="readonly",
                secret_enc=SecretBox(settings.encryption_key).encrypt(SecretStr("fake")),
                dialect="postgres",
            )
        )
        await session.flush()
        session.add(MetaColumn(id=21, table_id=2, name="salary", data_type="numeric", ordinal=1))
    await database.close()


@asynccontextmanager
async def running(settings):
    # The lifespan is entered in the test's own task, as the MCP session manager requires.
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.composition.cache = MemoryScopeCache()
        application.state.scope_cache = application.state.composition.cache
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=application), base_url=HOST
        ) as http:
            yield application, http


async def login(http, name):
    response = await http.post(
        "/api/auth/login", json={"username": name, "password": PASSWORDS[name]}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def mcp(application, key, mode="auto"):
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application),
        base_url=HOST,
        headers={"Authorization": f"Bearer {key}"},
    )
    return Client(streamable_http_client(f"{HOST}/mcp", http_client=http), mode=mode)


async def connect(http, admin, columns=(11, 12), **options):
    created = await http.post(
        "/api/mcp-clients", headers=admin, json={"name": "销售分析 Agent", **options}
    )
    assert created.status_code == 201, created.text
    client_id, key = created.json()["data"]["client_id"], created.json()["data"]["key"]
    granted = await http.put(
        f"/api/mcp-clients/{client_id}/grants/1",
        headers=admin,
        json={"columns": [{"column_id": c} for c in columns]},
    )
    assert granted.status_code == 200, granted.text
    return client_id, key


async def test_admin_api_and_mcp_endpoint_live_side_by_side(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        listed = await http.get("/api/mcp-clients", headers=admin)
        assert listed.status_code == 200 and listed.json()["data"] == []
        # /mcp is the protocol endpoint, not a prefix that swallows the admin API.
        assert (await http.post("/mcp", json={})).status_code == 401
        spec = (await http.get("/openapi.json")).json()
        assert "/api/mcp-clients" in spec["paths"] and "/mcp" not in spec["paths"]


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_a_connection_created_in_the_api_sees_exactly_its_grants(settings, mode):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        _, key = await connect(http, admin)
        async with mcp(application, key, mode) as session:
            sources = (await session.call_tool("list_datasources", {})).structured_content
            assert [s["datasource_id"] for s in sources["datasources"]] == [1]
            tables = await session.call_tool("list_tables", {"datasource_id": 1})
            assert [t["table"] for t in tables.structured_content["tables"]] == ["public.orders"]
            described = await session.call_tool(
                "describe_table", {"datasource_id": 1, "table": "public.orders"}
            )
            names = {c["name"] for c in described.structured_content["columns"]}
            assert names == {"id", "amount"}
            # Without an Embedding model, schema search still answers by keyword.
            found = await session.call_tool(
                "search_schema", {"datasource_id": 1, "query": "订单 amount"}
            )
            assert not found.is_error
            assert "salary" not in str(found.structured_content)
            assert "salary" not in str(described) and "phone" not in str(described)


async def test_ungranted_names_and_other_datasources_are_indistinguishable(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        _, key = await connect(http, admin, columns=(11,))
        await add_ungranted(settings, "salaries")
        async with mcp(application, key) as session:
            answers = []
            for arguments in (
                {"datasource_id": 1, "table": "public.salaries"},
                {"datasource_id": 1, "table": "public.nothing_here"},
            ):
                result = await session.call_tool("describe_table", arguments)
                assert result.is_error
                answers.append(
                    {k: v for k, v in result.structured_content.items() if k != "request_id"}
                )
                assert "salar" not in str(result)
            # An existing table the connection is not granted reads exactly like a missing one.
            assert answers[0] == answers[1]
            sources = []
            for datasource_id in (2, 99):
                result = await session.call_tool("list_tables", {"datasource_id": datasource_id})
                assert result.is_error
                sources.append(result.structured_content["code"])
            assert sources[0] == sources[1] == "DATASOURCE_UNAVAILABLE"


async def test_ask_without_a_configured_model_is_a_tool_error_that_was_counted(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        client_id, key = await connect(http, admin)
        async with mcp(application, key) as session:
            result = await session.call_tool(
                "ask", {"question": "上个月销售额", "datasource_id": 1}
            )
        assert result.is_error
        error = result.structured_content
        assert (error["code"], error["retryable"]) == ("LLM_UNAVAILABLE", True)
        assert error["request_id"].startswith("rq_")
        detail = await http.get(f"/api/mcp-clients/{client_id}", headers=admin)
        assert detail.json()["data"]["calls_today"] == 1


async def test_the_daily_limit_holds_on_the_real_endpoint(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        _, key = await connect(http, admin, daily_call_limit=1)
        async with mcp(application, key) as session:
            await session.call_tool("ask", {"question": "订单", "datasource_id": 1})
            limited = await session.call_tool("ask", {"question": "订单", "datasource_id": 1})
        assert limited.is_error and limited.structured_content["code"] == "QUOTA_EXCEEDED"


async def test_keys_stop_working_when_the_admin_says_so(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        client_id, key = await connect(http, admin)
        tools = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        accept = {"Accept": "application/json, text/event-stream"}

        async def status(secret):
            response = await http.post(
                "/mcp", json=tools, headers={**accept, "Authorization": f"Bearer {secret}"}
            )
            return response.status_code

        assert await status(key) == 200
        await http.post(
            f"/api/mcp-clients/{client_id}/status", headers=admin, json={"active": False}
        )
        assert await status(key) == 403
        await http.post(
            f"/api/mcp-clients/{client_id}/status", headers=admin, json={"active": True}
        )
        rotated = await http.post(
            f"/api/mcp-clients/{client_id}/keys/rotate", headers=admin, json={"grace_hours": 0}
        )
        fresh = rotated.json()["data"]["key"]
        assert (await status(key), await status(fresh)) == (401, 200)
        await http.delete(f"/api/mcp-clients/{client_id}", headers=admin)
        assert await status(fresh) == 401


@pytest.mark.parametrize("role", ["leader", "member"])
async def test_only_admins_reach_the_connection_api(settings, role):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        client_id, key = await connect(http, admin)
        headers = await login(http, role)
        requests = [
            ("GET", "/api/mcp-clients", None),
            ("POST", "/api/mcp-clients", {"name": "x"}),
            ("GET", f"/api/mcp-clients/{client_id}", None),
            ("PUT", f"/api/mcp-clients/{client_id}/grants/1", {"columns": [{"column_id": 13}]}),
            ("POST", f"/api/mcp-clients/{client_id}/keys/rotate", {"grace_hours": 0}),
            ("POST", f"/api/mcp-clients/{client_id}/test", {"question": "q", "datasource_id": 1}),
            ("DELETE", f"/api/mcp-clients/{client_id}", None),
        ]
        for method, path, body in requests:
            response = await http.request(method, path, headers=headers, json=body)
            assert response.status_code == 403, (method, path, response.text)
            assert key not in response.text
        assert (await http.get("/api/mcp-clients", headers=admin)).json()["data"]


async def test_a_service_account_cannot_use_the_web_api(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        client_id, key = await connect(http, admin)
        # A connection key is not a web token, and the service account cannot log in.
        assert (
            await http.get("/api/auth/me", headers={"Authorization": f"Bearer {key}"})
        ).status_code == 401
        users = (await http.get("/api/org/users", headers=admin)).json()["data"]
        names = (
            [u["username"] for u in users["items"]]
            if isinstance(users, dict)
            else [u["username"] for u in users]
        )
        assert all(not name.startswith("mcp-") for name in names)
        for name in [n for n in names if n.startswith("mcp-")] + ["mcp-x"]:
            denied = await http.post(
                "/api/auth/login", json={"username": name, "password": "!service"}
            )
            assert denied.status_code == 401


def leaked(text):
    words = set(re.findall(r"[A-Za-z_]+", text))
    return [name for name in HIDDEN if name in words or (not name.isascii() and name in text)]


async def test_no_tool_reveals_what_the_connection_is_not_granted(settings):
    assert len(UNSAFE) == 22
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        _, key = await connect(http, admin, allow_run_sql=True)
        await add_ungranted(settings, "salaries")
        async with mcp(application, key) as session:
            results = [
                await session.call_tool("list_datasources", {}),
                await session.call_tool("list_tables", {"datasource_id": 1}),
                await session.call_tool(
                    "describe_table", {"datasource_id": 1, "table": "public.orders"}
                ),
                await session.call_tool("list_metrics", {"datasource_id": 1}),
                await session.call_tool("list_glossary", {"datasource_id": 1}),
            ]
            for question in UNSAFE + ["员工底薪 salary 客户手机号 phone 人力成本"]:
                results.append(
                    await session.call_tool(
                        "search_schema", {"datasource_id": 1, "query": question}
                    )
                )
            for sql in (
                "SELECT salary FROM public.orders",
                "SELECT s.salary FROM public.salaries s",
                "SELECT * FROM public.orders",
                "SELECT table_name FROM information_schema.tables",
                "DELETE FROM public.orders",
                "SELECT pg_sleep(10)",
            ):
                refused = await session.call_tool("run_sql", {"datasource_id": 1, "sql": sql})
                assert refused.is_error, sql
                results.append(refused)
            tools = await session.list_tools()
        for result in results:
            text = json.dumps(
                [result.structured_content, [c.model_dump() for c in result.content]],
                ensure_ascii=False,
            )
            assert not leaked(text), (leaked(text), text[:300])
        assert not leaked(json.dumps([t.model_dump() for t in tools.tools], ensure_ascii=False))
        metrics = [i["name"] for i in results[3].structured_content["items"]]
        glossary = [a for i in results[4].structured_content["items"] for a in i["aliases"]]
        assert (metrics, glossary) == (["销售额"], ["实付金额"])


async def test_conversations_cannot_be_forged_or_kept_after_a_datasource_is_revoked(settings):
    async with running(settings) as (application, http):
        admin = await login(http, "admin")
        client_id, key = await connect(http, admin)
        async with mcp(application, key) as session:
            with pytest.raises(MCPError) as forged:
                await session.call_tool(
                    "ask",
                    {"question": "订单", "datasource_id": 1, "conversation_id": "conv_forged"},
                )
            assert "conversation" in str(forged.value)
            await http.put(
                f"/api/mcp-clients/{client_id}/grants/1", headers=admin, json={"columns": []}
            )
            revoked = await session.call_tool("ask", {"question": "订单", "datasource_id": 1})
            assert revoked.structured_content["code"] == "DATASOURCE_UNAVAILABLE"
            listed = await session.call_tool("list_datasources", {})
            assert listed.structured_content["datasources"] == []
