"""The MCP endpoint over real Streamable HTTP (in process): auth, both protocol generations,
tool vs protocol errors, progress, and per-request identity under concurrency."""

import asyncio
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Route

from app.core.config import Settings
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.mcp_gateway import build_endpoint
from app.modules.mcpaccess.schemas import AgentDataset, AskResult
from app.modules.mcpaccess.service import AgentOutcome, McpPrincipal, agent_error

KEYS = {"ak_alpha": (1, "alpha"), "ak_beta": (2, "beta")}


def principal(client_id: int, name: str) -> McpPrincipal:
    return McpPrincipal(
        user=CurrentUser(id=100 + client_id, role=Role.MEMBER, client_id=client_id),
        client_id=client_id,
        public_id=f"mc_{name}",
        name=name,
        default_rows=100,
        max_rows=1000,
        ask_timeout_seconds=60,
        allow_run_sql=False,
    )


class Access:
    async def authenticate(self, key):
        if key == "ak_disabled":
            raise AppError(ErrorCode.FORBIDDEN, http_status=403)
        if key not in KEYS:
            raise AppError(ErrorCode.AUTH_FAILED, http_status=401)
        return principal(*KEYS[key])


class Agent:
    seen: list[tuple[str, str]] = []

    async def datasources(self, who):
        await asyncio.sleep(0.01)
        return [{"datasource_id": who.client_id, "name": who.name, "dialect": "postgres"}]

    async def ask(self, who, question, datasource_id, ref, **options):
        if options.get("conversation_id") == "conv_unknown":
            raise AppError(ErrorCode.NOT_FOUND, "conversation not found", http_status=404)
        if question == "工资":
            return AgentOutcome(agent_error("SQL_OUT_OF_SCOPE", ref))
        if options.get("progress"):
            await options["progress"](2, "检索相关表和指标")
        Agent.seen.append((who.name, ref.request_id))
        return AgentOutcome(
            AskResult(
                status="success",
                request_id=ref.request_id,
                conversation_id=options.get("conversation_id") or f"conv_{who.name}",
                answer=f"{who.name}: 120 万",
                sql="SELECT 1",
                data=AgentDataset(
                    columns=["v"], rows=[{"v": 1}], row_count=1, returned_rows=1, truncated=False
                ),
            )
        )

    async def run_sql(self, who, sql, datasource_id, ref, **options):
        return AgentOutcome(agent_error("RUN_SQL_DISABLED", ref))


class Services:
    mcp = Access()

    def agent(self):
        return Agent()


class Runtime:
    @asynccontextmanager
    async def services(self, transaction=False):
        yield Services()


@asynccontextmanager
async def serving():
    # Entered and left in the test's own task: the session manager's task group requires it.
    server, app = build_endpoint(Settings(), lambda: Runtime())
    host = Starlette(routes=[Route("/mcp", endpoint=app, methods=["GET", "POST", "DELETE"])])
    async with server.session_manager.run():
        yield host


def client(app, key="ak_alpha", mode="auto", host="localhost:8000"):
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=f"http://{host}",
        headers={"Authorization": f"Bearer {key}"} if key else {},
    )
    return Client(streamable_http_client(f"http://{host}/mcp", http_client=http), mode=mode)


async def _post(app, headers, host="localhost:8000"):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=f"http://{host}"
    ) as http:
        return await http.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", **headers},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )


async def test_missing_bad_and_disabled_keys_never_reach_mcp():
    async with serving() as endpoint:
        assert (await _post(endpoint, {})).status_code == 401
        bad = await _post(endpoint, {"Authorization": "Bearer ak_wrong"})
        assert bad.status_code == 401 and "Bearer" in bad.headers["www-authenticate"]
        assert (await _post(endpoint, {"Authorization": "Bearer ak_disabled"})).status_code == 403


async def test_requests_for_other_hosts_are_refused():
    async with serving() as endpoint:
        # DNS rebinding protection: only configured Host headers are answered.
        response = await _post(endpoint, {"Authorization": "Bearer ak_alpha"}, host="evil.example")
        assert response.status_code == 421


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_both_protocol_generations_list_tools_and_ask(mode):
    async with serving() as endpoint:
        async with client(endpoint, mode=mode) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {
                "list_datasources",
                "list_tables",
                "describe_table",
                "search_schema",
                "list_metrics",
                "list_glossary",
                "ask",
                "run_sql",
            }
            assert "conversation_id" in tools["ask"].output_schema["properties"]
            result = await session.call_tool(
                "ask", {"question": "上个月销售额", "datasource_id": 1}
            )
            assert not result.is_error
            assert result.structured_content["conversation_id"] == "conv_alpha"
            assert result.structured_content["request_id"].startswith("rq_")


async def test_tool_failures_are_error_results_with_a_code():
    async with serving() as endpoint:
        async with client(endpoint) as session:
            result = await session.call_tool("ask", {"question": "工资", "datasource_id": 1})
            assert result.is_error
            assert result.structured_content["code"] == "SQL_OUT_OF_SCOPE"
            assert result.structured_content["retryable"] is False
            disabled = await session.call_tool("run_sql", {"datasource_id": 1, "sql": "SELECT 1"})
            assert disabled.is_error and disabled.structured_content["code"] == "RUN_SQL_DISABLED"


async def test_an_unknown_conversation_is_a_protocol_error():
    async with serving() as endpoint:
        async with client(endpoint) as session:
            with pytest.raises(MCPError):
                await session.call_tool(
                    "ask",
                    {"question": "那去年呢", "datasource_id": 1, "conversation_id": "conv_unknown"},
                )


async def test_invalid_arguments_never_reach_the_agent():
    async with serving() as endpoint:
        async with client(endpoint) as session:
            result = await session.call_tool("ask", {"question": "", "datasource_id": 0})
            assert result.is_error


async def test_progress_is_reported():
    async with serving() as endpoint:
        updates = []

        async def on_progress(progress, total, message):
            updates.append((progress, total, message))

        async with client(endpoint) as session:
            await session.call_tool(
                "ask", {"question": "销售额", "datasource_id": 1}, progress_callback=on_progress
            )
        assert (2, 6, "检索相关表和指标") in updates


async def test_concurrent_connections_each_act_as_themselves():
    async with serving() as endpoint:

        async def run(key, name):
            async with client(endpoint, key=key) as session:
                results = await asyncio.gather(
                    *(session.call_tool("list_datasources", {}) for _ in range(5))
                )
            return {r.structured_content["datasources"][0]["name"] for r in results}, name

        outcomes = await asyncio.gather(run("ak_alpha", "alpha"), run("ak_beta", "beta"))
        for names, name in outcomes:
            assert names == {name}


class Abandoned:
    """An ask that never finishes on its own; records how it ended."""

    def __init__(self):
        self.started, self.ended = asyncio.Event(), None

    async def ask(self, who, question, datasource_id, ref, **options):
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.ended = "cancelled"
            raise


@asynccontextmanager
async def over_a_socket():
    """The endpoint behind uvicorn on a free port: disconnects behave as in production."""
    import uvicorn

    async with serving() as host:
        server = uvicorn.Server(
            uvicorn.Config(host, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        )
        task = asyncio.create_task(server.serve())
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            yield f"http://localhost:{port}/mcp"
        finally:
            server.should_exit = True
            await task


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_an_abandoned_ask_stops_when_the_client_goes(monkeypatch, mode):
    # Best effort: the current protocol cancels the call itself; a legacy client on this
    # stateless endpoint stops it by closing its connection (the ask timeout bounds the rest).
    import anyio

    abandoned = Abandoned()
    monkeypatch.setattr(Agent, "ask", abandoned.ask)
    async with over_a_socket() as url:
        http = httpx2.AsyncClient(headers={"Authorization": "Bearer ak_alpha"})
        async with Client(streamable_http_client(url, http_client=http), mode=mode) as session:
            with anyio.move_on_after(0.3):
                await session.call_tool("ask", {"question": "q", "datasource_id": 1})
            assert abandoned.started.is_set()
            await asyncio.sleep(0.3)
            if mode == "auto":
                assert abandoned.ended == "cancelled"
        for _ in range(50):
            if abandoned.ended:
                break
            await asyncio.sleep(0.02)
        assert abandoned.ended == "cancelled"
