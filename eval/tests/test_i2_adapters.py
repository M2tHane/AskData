"""Run with backend's environment: pytest -c pyproject.toml ../eval/tests/test_i2_adapters.py.

The asset-only eval environment has no backend dependencies and skips this module.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("app", reason="requires the backend environment")

from app.core.audit import QueryLogRecord, QueryLogWriter  # noqa: E402
from app.core.datasource import (  # noqa: E402
    ColumnMetadata,
    ConnectorRegistry,
    Datasource,
    TableMetadata,
)
from app.core.deps import CurrentUser, Role  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.core.permission import PermissionService  # noqa: E402
from app.core.sql.dialect import Dialect  # noqa: E402
from app.core.sql.executor import SqlExecutor  # noqa: E402
from app.core.sql.guard import SqlGuard  # noqa: E402
from app.modules.eval.adapters import (  # noqa: E402
    FixtureBindings,
    build_fixture_assets,
    build_reference_runner,
    build_simulated_adapter,
    rewrite_schema,
    run_suite,
)
from app.modules.eval.assets import EvalAssets  # noqa: E402
from app.modules.eval.schemas import RunRequest  # noqa: E402
from app.modules.eval.service import EvalQuestion  # noqa: E402
from app.modules.schema.service import SyncResult  # noqa: E402
from app.modules.upload.adapters import (  # noqa: E402
    UploadPublication,
    build_upload_registration,
)
from pydantic import SecretStr  # noqa: E402
from tests.fakes import FakeConnector, MemoryScopeCache  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Logs:
    def __init__(self) -> None:
        self.records: list[QueryLogRecord] = []

    async def append(self, record: QueryLogRecord) -> int:
        self.records.append(record)
        return len(self.records)


@pytest.fixture
def mapped() -> Any:
    assets = EvalAssets(ROOT / "eval")
    teams = {name: identifier + 70 for name, identifier in assets.team_ids.items()}
    users = {
        alias: user.model_copy(
            update={
                "id": user.id + 300,
                "team_id": user.team_id + 70 if user.team_id else None,
            }
        )
        for alias, user in assets.users.items()
    }
    datasources = {
        alias: Datasource(
            id=identifier + 200,
            dialect=Dialect.POSTGRES,
            host="offline",
            database="eval",
            username="reader",
            password=SecretStr(""),
            schema_name="eval_commerce" if alias == "commerce" else f"ds_{identifier + 200}",
            tls=False,
            port=5432,
        )
        for alias, identifier in assets.datasource_ids.items()
    }
    return build_fixture_assets(
        ROOT / "eval", FixtureBindings(team_ids=teams, users=users, datasources=datasources)
    )


def question(assets: Any, case: Any) -> EvalQuestion:
    return EvalQuestion(
        case_id=case.id,
        datasource=case.datasource,
        as_user=case.as_user,
        question=case.question,
        history=case.history,
        clock=assets.fixtures["clock"],
        injection_context=case.injection_context,
    )


def script(sql: str, chart: dict[str, Any] | None = None) -> list[str]:
    return [
        json.dumps({"clarify": False}),
        json.dumps({"sql": sql}),
        json.dumps(chart or {"type": "table"}),
        json.dumps({"summary": "查询完成"}),
    ]


async def test_all_22_gate_cases_use_real_chat_pipeline_and_never_connect(mapped: Any) -> None:
    connector = FakeConnector()
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    logs = Logs()
    safety = [case for case in mapped.cases if case.expect_status == "blocked"]
    scripts = {
        case.id: script(mapped.map_sql(case.datasource, case.candidate_sql)) for case in safety
    }
    adapter = build_simulated_adapter(mapped, scripts, executor, QueryLogWriter(logs))
    results, summary = await run_suite(
        mapped,
        adapter,
        build_reference_runner(mapped, SqlGuard(), executor),
        RunRequest(model_id="fixed", case_ids=[case.id for case in safety]),
    )
    assert len(results) == 22
    assert all(item.passed for item in results), [(r.case_id, r.reason) for r in results]
    assert summary["safety_gate_passed"] and summary["blocked_pass_rate"] == 1
    assert connector.opened == 0 and not connector.executed and not connector.explained
    assert len(logs.records) == 22
    for record in logs.records:
        assert record.status == "blocked" and record.retry_count == 0
        assert record.user_id >= 301 and record.datasource_id >= 201
        assert "sql_guard" in record.stage_timings and "execute" not in record.stage_timings
        assert record.prompt_versions == {"clarification": "v1", "sql_generation": "v1"}


async def test_success_and_reference_share_physical_schema_guard_but_not_counters(
    mapped: Any,
) -> None:
    case = next(case for case in mapped.cases if case.id == "S01")
    connector = FakeConnector([{"n": 3}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    logs = Logs()
    adapter = build_simulated_adapter(
        mapped,
        {case.id: script(mapped.map_sql(case.datasource, case.expected_sql))},
        executor,
        QueryLogWriter(logs),
    )
    reference = await build_reference_runner(mapped, SqlGuard(), executor).result(case)
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert outcome.result is not None
    assert reference.rows == outcome.result.rows == [{"n": 3}]
    assert outcome.status == "success" and outcome.executed_queries == 1
    assert len(connector.executed) == 2 and connector.opened == 2
    assert all('"eval_commerce".' in sql for sql in connector.executed)
    assert outcome.chart_valid and outcome.chart_type == "table"
    assert len(logs.records[0].result_preview) == 1


async def test_history_clock_followup_and_time_range_from_actual_context(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "T08")
    connector = FakeConnector([{"n": 3}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    logs = Logs()
    adapter = build_simulated_adapter(
        mapped,
        {case.id: script(mapped.map_sql(case.datasource, case.expected_sql))},
        executor,
        QueryLogWriter(logs),
    )
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    record = logs.records[0]
    assert record.details["follow_up"]
    assert '"eval_commerce".' in record.details["inherited_sql"]
    assert record.details["time_ranges"]["today"] == "2025-03-15"
    assert outcome.time_range == case.expected_time_range
    assert record.details["history"][-1]["normalized_sql"].startswith("SELECT")


async def test_chart_fallback_cannot_claim_model_chart_valid(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "C01")
    connector = FakeConnector([{"n": 3}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    adapter = build_simulated_adapter(
        mapped,
        {
            case.id: script(
                mapped.map_sql(case.datasource, case.expected_sql), {"type": "javascript"}
            )
        },
        executor,
        QueryLogWriter(Logs()),
    )
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert outcome.status == "success" and outcome.chart_type == "table"
    assert not outcome.chart_valid


async def test_concurrent_cases_do_not_share_execution_counts(mapped: Any) -> None:
    success = next(case for case in mapped.cases if case.id == "S01")
    blocked = next(case for case in mapped.cases if case.id == "P01")
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: FakeConnector([{"n": 3}])}))
    adapter = build_simulated_adapter(
        mapped,
        {
            success.id: script(mapped.map_sql(success.datasource, success.expected_sql)),
            blocked.id: script(mapped.map_sql(blocked.datasource, blocked.candidate_sql)),
        },
        executor,
        QueryLogWriter(Logs()),
    )
    good, bad = await asyncio.gather(
        adapter.run(question(mapped, success), RunRequest(model_id="fixed")),
        adapter.run(question(mapped, blocked), RunRequest(model_id="fixed")),
    )
    assert good.executed_queries == 1 and bad.executed_queries == 0


async def test_correction_guard_block_counts_the_actual_first_attempt(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "S01")

    class UndefinedColumn(Exception):
        sqlstate = "42703"

    connector = FakeConnector()
    connector.error = UndefinedColumn("must not leak")
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    adapter = build_simulated_adapter(
        mapped,
        {
            case.id: script(mapped.map_sql(case.datasource, case.expected_sql))[:2]
            + [json.dumps({"sql": "DELETE FROM eval_commerce.orders"})]
        },
        executor,
        QueryLogWriter(Logs()),
    )
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert outcome.status == "blocked" and outcome.blocked_code == ErrorCode.SQL_NOT_SELECT
    assert outcome.executed_queries == outcome.retries == 1
    assert "must not leak" not in outcome.message


async def test_missing_script_and_unsupported_prompt_version_fail_closed(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "S01")
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: FakeConnector()}))
    adapter = build_simulated_adapter(mapped, {}, executor, QueryLogWriter(Logs()))
    with pytest.raises(AppError):
        await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    adapter = build_simulated_adapter(mapped, {case.id: []}, executor, QueryLogWriter(Logs()))
    with pytest.raises(AppError):
        await adapter.run(
            question(mapped, case),
            RunRequest(model_id="fixed", prompt_versions={"sql_generation": "missing"}),
        )


async def test_partial_safety_run_cannot_authorize_release(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "P01")
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: FakeConnector()}))
    adapter = build_simulated_adapter(
        mapped,
        {case.id: script(mapped.map_sql(case.datasource, case.candidate_sql))},
        executor,
        QueryLogWriter(Logs()),
    )
    results, summary = await run_suite(
        mapped,
        adapter,
        build_reference_runner(mapped, SqlGuard(), executor),
        RunRequest(model_id="fixed", case_ids=[case.id]),
    )
    assert results[0].passed and not summary["safety_gate_passed"]


async def test_dependency_factory_receives_only_input_and_wrong_model_never_executes(
    mapped: Any,
) -> None:
    from app.modules.eval.adapters import build_fixed_client

    case = next(case for case in mapped.cases if case.id == "S01")
    connector = FakeConnector([{"n": 3}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    adapter = build_simulated_adapter(
        mapped,
        {case.id: script(mapped.map_sql(case.datasource, case.expected_sql))},
        executor,
        QueryLogWriter(Logs()),
    )
    original = adapter.dependencies
    seen = []

    async def dependencies(input_question: EvalQuestion, config: RunRequest) -> Any:
        seen.append(input_question.model_dump())
        deps = await original(input_question, config)
        deps.llm = build_fixed_client([], model_id="different")
        return deps

    adapter.dependencies = dependencies
    with pytest.raises(AppError):
        await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert not connector.opened
    assert set(seen[0]) == {
        "case_id",
        "datasource",
        "as_user",
        "question",
        "history",
        "clock",
        "injection_context",
    }


@pytest.mark.parametrize("drift", ["team", "role", "inactive", "missing", "duplicate"])
def test_fixture_binding_requires_exact_identity_contract(mapped: Any, drift: str) -> None:
    bindings = mapped.bindings.model_copy(deep=True)
    user = bindings.users["sales_member"]
    if drift == "team":
        bindings.users["sales_member"] = user.model_copy(
            update={"team_id": bindings.team_ids["finance"]}
        )
    elif drift == "role":
        bindings.users["sales_member"] = user.model_copy(update={"role": Role.ADMIN})
    elif drift == "inactive":
        bindings.users["sales_member"] = user.model_copy(update={"active": False})
    elif drift == "missing":
        del bindings.users["sales_member"]
    else:
        bindings.users["sales_member"] = user.model_copy(
            update={"id": bindings.users["sales_leader"].id}
        )
    with pytest.raises(AppError):
        build_fixture_assets(ROOT / "eval", bindings)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM commerce.orders", 'SELECT * FROM "eval_commerce".orders'),
        (
            'SELECT * FROM "commerce" /* unchanged commerce.x */ .orders',
            'SELECT * FROM "eval_commerce" /* unchanged commerce.x */ .orders',
        ),
        (
            "SELECT 'commerce.orders' FROM commerce.orders -- commerce.x",
            "SELECT 'commerce.orders' FROM \"eval_commerce\".orders -- commerce.x",
        ),
        (
            "SELECT $$commerce.orders$$, $tag$commerce.orders$tag$ FROM commerce.orders",
            'SELECT $$commerce.orders$$, $tag$commerce.orders$tag$ FROM "eval_commerce".orders',
        ),
        ("SELECT * FROM commerce_extra.orders", "SELECT * FROM commerce_extra.orders"),
    ],
)
def test_schema_mapping_preserves_literal_comment_and_longer_names(sql: str, expected: str) -> None:
    assert rewrite_schema(sql, "commerce", "eval_commerce") == expected


def test_schema_mapping_rejects_ambiguous_lexing_and_invalid_target() -> None:
    with pytest.raises(AppError):
        rewrite_schema("SELECT * FROM commerce.orders", "commerce", "other;DELETE")
    with pytest.raises(AppError):
        rewrite_schema("/* /* nested */ commerce.x */ SELECT 1", "commerce", "mapped")


def test_cli_invalid_environment_never_echoes_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from run_pipeline import main

    artifact = tmp_path / "responses.json"
    artifact.write_text("{}", encoding="utf-8")
    marker = "synthetic-secret-must-never-appear"
    monkeypatch.setenv("ASKDATA_EVALUATION_DATASOURCES", "malformed-json-" + marker)
    monkeypatch.setattr("sys.argv", ["run_pipeline", "--responses", str(artifact)])
    assert main() == 2
    captured = capsys.readouterr()
    assert marker not in captured.err + captured.out
    assert "Traceback" not in captured.err
    assert "INTERNAL_ERROR" in captured.err and not captured.out


class UploadServices:
    """Fake public ports; durable receipts are kept on the service, not the adapter."""

    def __init__(self) -> None:
        self.owners: dict[int, int] = {}
        self.receipts: set[int] = set()
        self.published: set[int] = set()
        self.calls: list[tuple[Any, ...]] = []

    async def ensure_team(self, team_id: int) -> object:
        self.calls.append(("team", team_id))
        return object()

    async def reserve_upload(self, name: str, team_id: int, *, actor: CurrentUser) -> int:
        self.calls.append(("reserve", actor))
        identifier = len(self.owners) + 1
        self.owners[identifier] = team_id
        return identifier

    async def upload_owner(self, datasource_id: int) -> int:
        if datasource_id not in self.owners:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        return self.owners[datasource_id]

    async def publish_upload_credentials(
        self, job_id: int, datasource: Datasource, owner_team_id: int, *, actor: CurrentUser
    ) -> UploadPublication:
        self.calls.append(("credentials", actor))
        return UploadPublication(
            datasource_id=datasource.id,
            owner_team_id=owner_team_id,
            completed=job_id in self.receipts,
            initialize_owner_grants=datasource.id not in self.published,
        )

    async def finish_upload_publication(
        self, job_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None:
        self.receipts.add(job_id)
        self.published.add(datasource_id)
        self.calls.append(("finish", actor))

    async def synchronize_upload(
        self, datasource_id: int, table: TableMetadata, *, actor: CurrentUser
    ) -> SyncResult:
        self.calls.append(("schema", table, actor))
        return SyncResult(
            datasource_id=datasource_id, removed_tables=[], removed_columns=[42], table_count=1
        )

    async def expire_metadata(self, result: SyncResult) -> None:
        self.calls.append(("expire", result.removed_columns))

    async def grant_upload_owner(
        self, datasource_id: int, team_id: int, *, actor: CurrentUser
    ) -> None:
        self.calls.append(("grant", datasource_id, team_id, actor))

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[Any]:
        return []


def upload_source(identifier: int = 1) -> Datasource:
    return Datasource(
        id=identifier,
        dialect=Dialect.POSTGRES,
        host="offline",
        port=5432,
        database="import",
        username="reader",
        password=SecretStr(""),
        schema_name=f"ds_{identifier}",
    )


async def test_upload_services_preserve_actor_owner_grants_receipts_and_removed_columns() -> None:
    services, cache = UploadServices(), MemoryScopeCache()
    permissions = PermissionService(services, cache)
    adapter = build_upload_registration(services, services, services, services, permissions)
    leader = CurrentUser(id=9, role=Role.LEADER, team_id=3)
    identifier = await adapter.reserve("sheet", 3, leader)
    table = TableMetadata(
        schema_name="ds_1",
        name="sales",
        columns=[ColumnMetadata(name="amount", data_type="numeric", comment="原金额", ordinal=1)],
    )
    await adapter.publish(10, upload_source(identifier), table, 3, leader)
    # A new adapter instance must retain idempotence across process/service reconstruction.
    await build_upload_registration(services, services, services, services, permissions).publish(
        10, upload_source(identifier), table, 3, leader
    )
    await adapter.publish(11, upload_source(identifier), table, 3, leader)
    assert [call for call in services.calls if call[0] == "grant"] == [("grant", 1, 3, leader)]
    assert len([call for call in services.calls if call[0] == "schema"]) == 2
    assert ("expire", [42]) in services.calls and cache.generation == 2
    assert all(
        call[-1] is leader
        for call in services.calls
        if call[0] in {"reserve", "credentials", "schema", "grant", "finish"}
    )


async def test_upload_rejects_missing_ports_roles_owners_and_writable_sources() -> None:
    services = UploadServices()
    permissions = PermissionService(services)
    with pytest.raises(AppError) as missing:
        build_upload_registration(object(), services, services, services, permissions)
    assert missing.value.http_status == 503
    adapter = build_upload_registration(services, services, services, services, permissions)
    leader = CurrentUser(id=9, role=Role.LEADER, team_id=3)
    for actor, team in [(leader, 4), (CurrentUser(id=8, role=Role.MEMBER, team_id=3), 3)]:
        with pytest.raises(AppError):
            await adapter.reserve("sheet", team, actor)
    assert not services.calls
    await adapter.reserve("sheet", 3, leader)
    table = TableMetadata(schema_name="ds_1", name="sales", columns=[])
    for datasource, owner in [
        (upload_source().model_copy(update={"read_only": False}), 3),
        (upload_source().model_copy(update={"schema_name": "public"}), 3),
        (upload_source(), 4),
    ]:
        with pytest.raises(AppError):
            await adapter.publish(10, datasource, table, owner, leader)
    assert not any(call[0] == "credentials" for call in services.calls)
    with pytest.raises(AppError):
        await adapter.owner_team(99)
