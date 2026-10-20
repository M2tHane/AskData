"""AgentService over the real query pipeline (scripted model, fake database): multi-turn
conversations, revocation between turns, result budgets, timeouts and clarification."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.masking import MaskKind, MaskRule
from app.core.permission import PermissionService, TableGrant
from app.modules.mcpaccess.schemas import CHAR_BUDGET
from app.modules.mcpaccess.service import AgentService, CallRef, McpPrincipal
from tests.test_chat import system as chat_system  # noqa: F401 - fixture

pytest_plugins = ["tests.test_chat"]

PRINCIPAL = McpPrincipal(
    user=CurrentUser(id=101, role=Role.MEMBER, client_id=1),
    client_id=1,
    public_id="mc_alpha",
    name="alpha",
    default_rows=100,
    max_rows=1000,
    ask_timeout_seconds=60,
    allow_run_sql=False,
)
OTHER = replace(PRINCIPAL, user=CurrentUser(id=102, role=Role.MEMBER, client_id=2), client_id=2)


class ClientGrants:
    """Each connection's allowlist; tests edit it between turns."""

    def __init__(self, scope):
        self.grants = {
            client: [
                TableGrant(
                    name=name,
                    all_columns=table.all_columns,
                    team_visible=True,
                    member_visible=True,
                    team_columns=table.columns,
                    member_columns=table.columns,
                )
                for name, table in scope.tables.items()
            ]
            for client in (1, 2)
        }

    async def client_snapshot(self, datasource_id, client_id):
        return [grant.model_copy(deep=True) for grant in self.grants.get(client_id, [])]

    def set_columns(self, client_id, table, columns, masks=None):
        grant = next(g for g in self.grants[client_id] if g.name == table)
        grant.team_columns = grant.member_columns = frozenset(columns)
        grant.masks = masks or {}


class Bindings:
    def __init__(self):
        self.rows = {}

    async def commit(self):
        pass


class Access:
    """The connection service's runtime side, in memory."""

    def __init__(self, permissions):
        self.repository = Bindings()
        self.grants = SimpleNamespace(permissions=permissions)
        self.calls = 0
        self.limit = 1000

    async def conversation(self, principal, public_id):
        row = self.repository.rows.get(public_id)
        if row is None or row.client_id != principal.client_id:
            raise AppError(ErrorCode.NOT_FOUND, "conversation not found", http_status=404)
        return row

    async def bind_conversation(self, principal, conversation_id, datasource_id):
        public_id = f"conv_{len(self.repository.rows) + 1}"
        row = SimpleNamespace(
            public_id=public_id,
            client_id=principal.client_id,
            conversation_id=conversation_id,
            datasource_id=datasource_id,
            pending_message_id=None,
        )
        self.repository.rows[public_id] = row
        return row

    @staticmethod
    def touch(row, pending):
        row.pending_message_id = pending

    async def admit(self, principal):
        self.calls += 1
        if self.calls > self.limit:
            raise AppError(ErrorCode.QUOTA_EXCEEDED, http_status=429)

    async def visible_datasource(self, principal, datasource_id):
        scope = await self.grants.permissions.visible_scope(principal.user, datasource_id)
        return bool(scope.tables)

    async def overridden(self, client_id, kind):
        return set()


@pytest.fixture
def agent(chat_system, scope):  # noqa: F811
    service, transport, connector, logs, _, _ = chat_system
    clients = ClientGrants(scope)
    permissions = PermissionService(service.pipeline.deps.permissions.provider, clients=clients)
    service.pipeline.deps.permissions = permissions
    access = Access(permissions)
    return AgentService(access, service), transport, connector, logs, clients, access


async def test_a_follow_up_continues_the_conversation_it_names(agent):
    service, transport, connector, logs, _, _ = agent
    first = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    assert first.result.status == "success" and first.result.conversation_id == "conv_1"
    second = await service.ask(
        PRINCIPAL, "那去年同期呢", 1, CallRef(), conversation_id=first.result.conversation_id
    )
    assert second.result.conversation_id == "conv_1"
    assert logs.records[-1].details["inherited_sql"]
    assert logs.records[-1].conversation_id == logs.records[0].conversation_id


async def test_runs_are_logged_with_their_source_and_request_id(agent):
    service, _, _, logs, _, _ = agent
    ref = CallRef(rpc_id="7")
    outcome = await service.ask(PRINCIPAL, "上个月订单", 1, ref)
    record = logs.records[-1]
    assert outcome.result.request_id == ref.request_id == record.request_id
    assert (record.source, record.mcp_client_id) == ("mcp", 1)
    assert record.details["origin"]["rpc_id"] == "7"


async def test_another_connections_conversation_cannot_be_continued(agent):
    service, *_ = agent
    first = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    with pytest.raises(AppError) as stolen:
        await service.ask(
            OTHER, "那去年呢", 1, CallRef(), conversation_id=first.result.conversation_id
        )
    assert stolen.value.code == ErrorCode.NOT_FOUND


async def test_a_conversation_stays_on_its_datasource(agent):
    service, *_ = agent
    first = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    moved = await service.ask(
        PRINCIPAL, "那呢", 2, CallRef(), conversation_id=first.result.conversation_id
    )
    assert moved.is_error and moved.result.code == "CONVERSATION_DATASOURCE_MISMATCH"


async def test_revoked_columns_drop_earlier_answers_from_a_follow_up(agent):
    service, transport, connector, logs, clients, _ = agent
    transport.sql = "SELECT amount FROM orders"
    connector.rows = [{"amount": 9000}]
    first = await service.ask(PRINCIPAL, "订单金额", 1, CallRef())
    assert first.result.status == "success"
    clients.set_columns(1, "orders", {"id", "customer_id"})
    transport.sql = "SELECT id FROM orders"
    connector.rows = [{"id": 1}]
    await service.ask(PRINCIPAL, "按客户拆开", 1, CallRef(), conversation_id="conv_1")
    details = logs.records[-1].details
    # Yesterday's answer about amount is neither shown to the model nor inherited.
    assert details["inherited_sql"] is None
    assert all(m["role"] == "user" for m in details["history"])
    transport.sql = "SELECT amount FROM orders"
    refused = await service.ask(PRINCIPAL, "再看金额", 1, CallRef(), conversation_id="conv_1")
    assert refused.is_error and refused.result.code == "SQL_OUT_OF_SCOPE"
    assert "amount" not in refused.result.message and "orders" not in refused.result.message


async def test_an_answer_written_before_a_mask_is_not_replayed_as_text(agent):
    service, transport, connector, logs, clients, _ = agent
    transport.sql = "SELECT phone FROM orders"
    transport.responses["answer"] = "手机号是 13812345678"
    connector.rows = [{"phone": "13812345678"}]
    await service.ask(PRINCIPAL, "客户手机号", 1, CallRef())
    clients.set_columns(
        1,
        "orders",
        {"id", "customer_id", "amount", "phone", "tenant_id"},
        {"phone": MaskRule(kind=MaskKind.PHONE)},
    )
    transport.messages.clear()
    await service.ask(PRINCIPAL, "再看一次", 1, CallRef(), conversation_id="conv_1")
    # The model saw the earlier SQL to follow up on, never the unmasked number.
    prompts = "".join(transport.messages)
    assert "SELECT phone" in prompts or "phone" in prompts
    assert "13812345678" not in prompts


async def test_rows_are_capped_by_count_and_by_characters(agent):
    service, transport, connector, _, _, _ = agent
    connector.rows = [{"id": n} for n in range(500)]
    capped = await service.ask(PRINCIPAL, "所有订单", 1, CallRef())
    data = capped.result.data
    assert (data.returned_rows, data.row_count, data.truncated_by) == (100, 500, "rows")
    more = await service.ask(PRINCIPAL, "所有订单", 1, CallRef(), max_rows=5000)
    assert more.result.data.returned_rows == 500
    transport.sql = "SELECT phone FROM orders"
    connector.rows = [{"phone": "x" * 2000} for _ in range(50)]
    wide = await service.ask(PRINCIPAL, "手机号", 1, CallRef())
    assert wide.result.data.truncated_by == "chars"
    assert wide.result.data.returned_rows * 2000 <= CHAR_BUDGET


async def test_slow_questions_time_out_as_a_retryable_error(agent):
    service, _, connector, logs, _, _ = agent
    connector.wait = asyncio.Event()
    slow = replace(PRINCIPAL, ask_timeout_seconds=0.05)
    outcome = await service.ask(slow, "上个月订单", 1, CallRef())
    assert outcome.is_error and outcome.result.code == "TIMEOUT" and outcome.result.retryable
    assert logs.records[-1].error == ErrorCode.QUERY_CANCELLED


async def test_clarification_is_answered_with_a_choice_in_the_same_conversation(agent):
    service, *_ = agent
    asked = await service.ask(PRINCIPAL, "最近订单", 1, CallRef())
    assert asked.result.status == "clarify" and asked.result.options == ["近7天", "近30天"]
    answered = await service.ask(
        PRINCIPAL,
        "最近订单",
        1,
        CallRef(),
        conversation_id=asked.result.conversation_id,
        choice="近7天",
    )
    assert answered.result.status == "success"


async def test_daily_limit_and_hidden_datasources_stop_before_the_model(agent):
    service, transport, _, _, clients, access = agent
    access.limit = 0
    limited = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    assert limited.result.code == "QUOTA_EXCEEDED" and not transport.messages
    clients.grants[1] = []
    hidden = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    assert hidden.result.code == "DATASOURCE_UNAVAILABLE"


async def test_run_sql_is_off_by_default_and_guarded_when_on(agent):
    service, _, connector, logs, clients, _ = agent
    off = await service.run_sql(PRINCIPAL, "SELECT id FROM orders", 1, CallRef())
    assert off.result.code == "RUN_SQL_DISABLED" and not connector.executed
    allowed = replace(PRINCIPAL, allow_run_sql=True)
    blocked = await service.run_sql(allowed, "SELECT secret FROM orders", 1, CallRef())
    assert blocked.result.code == "SQL_OUT_OF_SCOPE" and not connector.executed
    write = await service.run_sql(allowed, "DELETE FROM orders", 1, CallRef())
    assert write.is_error and not connector.executed
    connector.rows = [{"phone": "13812345678"}]
    clients.set_columns(
        1,
        "orders",
        {"id", "phone"},
        {"phone": MaskRule(kind=MaskKind.PHONE)},
    )
    masked = await service.run_sql(allowed, "SELECT phone FROM orders", 1, CallRef())
    assert masked.result.status == "success"
    assert masked.result.data.rows[0]["phone"] != "13812345678"
    assert logs.records[-1].source == "mcp"


async def test_progress_only_moves_forward(agent):
    service, transport, _, _, _, _ = agent
    transport.corrections = []
    steps = []

    async def progress(step, message):
        steps.append(step)

    await service.ask(PRINCIPAL, "上个月订单", 1, CallRef(), progress=progress)
    assert steps == sorted(set(steps)) and len(steps) >= 4


async def test_a_question_the_caller_abandons_is_logged_as_cancelled(agent):
    import anyio

    service, _, connector, logs, _, access = agent
    connector.wait = asyncio.Event()
    with anyio.move_on_after(0.05) as scope:
        await service.ask(PRINCIPAL, "上个月订单", 1, CallRef(rpc_id="9"))
    assert scope.cancelled_caught
    record = logs.records[-1]
    assert record.error == ErrorCode.QUERY_CANCELLED and record.source == "mcp"
    # It was admitted, so it counts against the day's calls.
    assert access.calls == 1


async def test_a_conversation_goes_on_after_a_turn_timed_out(agent):
    service, _, connector, logs, _, _ = agent
    first = await service.ask(PRINCIPAL, "上个月订单", 1, CallRef())
    conversation = first.result.conversation_id
    connector.wait = asyncio.Event()
    slow = replace(PRINCIPAL, ask_timeout_seconds=0.05)
    timed_out = await service.ask(slow, "那去年同期呢", 1, CallRef(), conversation_id=conversation)
    assert timed_out.result.code == "TIMEOUT"
    connector.wait = None
    again = await service.ask(PRINCIPAL, "那去年同期呢", 1, CallRef(), conversation_id=conversation)
    assert again.result.status == "success" and again.result.conversation_id == conversation
    # The follow-up still builds on the answered turn, not on the one that never finished.
    assert logs.records[-1].details["inherited_sql"]


async def test_a_choice_outside_the_options_is_refused_and_the_question_stays_open(agent):
    service, *_ = agent
    asked = await service.ask(PRINCIPAL, "最近订单", 1, CallRef())
    conversation = asked.result.conversation_id
    with pytest.raises(AppError) as invalid:
        await service.ask(
            PRINCIPAL, "最近订单", 1, CallRef(), conversation_id=conversation, choice="近一年"
        )
    assert invalid.value.code == ErrorCode.VALIDATION_ERROR
    answered = await service.ask(
        PRINCIPAL, "最近订单", 1, CallRef(), conversation_id=conversation, choice="近7天"
    )
    assert answered.result.status == "success"


async def test_a_choice_with_no_open_question_is_a_new_question(agent):
    service, transport, *_ = agent
    asked = await service.ask(PRINCIPAL, "最近订单", 1, CallRef())
    conversation = asked.result.conversation_id
    await service.ask(
        PRINCIPAL, "最近订单", 1, CallRef(), conversation_id=conversation, choice="近7天"
    )
    # Replaying the same choice cannot answer the clarification twice.
    again = await service.ask(
        PRINCIPAL, "上个月订单", 1, CallRef(), conversation_id=conversation, choice="近7天"
    )
    assert again.result.status == "success"


async def test_run_sql_refuses_several_statements_caps_rows_and_counts(agent):
    service, _, connector, _, _, access = agent
    allowed = replace(PRINCIPAL, allow_run_sql=True)
    stacked = await service.run_sql(
        allowed, "SELECT id FROM orders; SELECT id FROM orders", 1, CallRef()
    )
    assert stacked.is_error and not connector.executed
    connector.rows = [{"id": n} for n in range(500)]
    capped = await service.run_sql(allowed, "SELECT id FROM orders", 1, CallRef(), max_rows=20)
    assert (capped.result.data.returned_rows, capped.result.data.truncated_by) == (20, "rows")
    assert access.calls == 2
    access.limit = 2
    limited = await service.run_sql(allowed, "SELECT id FROM orders", 1, CallRef())
    assert limited.result.code == "QUOTA_EXCEEDED"


async def test_an_admin_test_ask_runs_as_the_connection_and_is_logged_apart(agent):
    from app.core.deps import CurrentUser as User
    from app.modules.mcpaccess.schemas import TestAskRequest

    service, _, _, logs, _, access = agent

    async def test_principal(client_id, *, actor):
        assert actor.role == Role.ADMIN
        return PRINCIPAL

    access.test_principal = test_principal
    reply = await service.test(
        1,
        TestAskRequest(question="上个月订单", datasource_id=1),
        actor=User(id=1, role=Role.ADMIN),
    )
    assert not reply.is_error and reply.agent["status"] == "success"
    record = logs.records[-1]
    assert (record.source, record.mcp_client_id) == ("mcp_test", 1)
    # Testing spends the connection's daily calls like a real agent would.
    assert access.calls == 1
    assert reply.diagnostics.guard == "passed" and reply.diagnostics.tables
    # What the agent would get carries none of the admin's diagnostics.
    assert not {"guard", "tables", "columns", "principal"} & set(reply.agent)
    unknown = await service.test(
        1,
        TestAskRequest(question="那呢", datasource_id=1, conversation_id="conv_none"),
        actor=User(id=1, role=Role.ADMIN),
    )
    assert unknown.is_error and unknown.agent["code"] == "INVALID_PARAMS"


async def test_errors_report_the_tokens_they_spent(agent):
    service, transport, connector, _, clients, access = agent
    transport.sql = "SELECT secret FROM orders"
    blocked = await service.ask(PRINCIPAL, "订单里的秘密", 1, CallRef())
    assert blocked.is_error and blocked.result.code == "SQL_OUT_OF_SCOPE"
    # The model wrote SQL before the guard refused it: that was paid for.
    assert blocked.result.usage.input_tokens > 0 and blocked.result.usage.output_tokens > 0
    transport.sql = "SELECT id FROM orders"
    connector.wait = asyncio.Event()
    slow = await service.ask(replace(PRINCIPAL, ask_timeout_seconds=0.05), "订单", 1, CallRef())
    assert slow.result.code == "TIMEOUT" and slow.result.usage.duration_ms >= 50
    # Refused before any model call: zero, but always present.
    access.limit = 0
    limited = await service.ask(PRINCIPAL, "订单", 1, CallRef())
    assert limited.result.code == "QUOTA_EXCEEDED"
    assert limited.result.usage.model_dump() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": 0,
    }
