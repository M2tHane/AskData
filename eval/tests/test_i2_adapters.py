"""Run with backend's environment: pytest -c pyproject.toml ../eval/tests/test_i2_adapters.py.

The asset-only eval environment has no backend dependencies and skips this module.
"""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("app", reason="requires the backend environment")

from app.core.audit import QueryLogRecord, QueryLogWriter  # noqa: E402
from app.core.datasource import (  # noqa: E402
    ConnectorRegistry,
    Datasource,
)
from app.core.deps import Role  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.sql.dialect import Dialect  # noqa: E402
from app.core.sql.executor import SqlExecutor  # noqa: E402
from app.modules.eval.adapters import (  # noqa: E402
    FixtureBindings,
    build_fixture_assets,
    build_simulated_adapter,
    rewrite_schema,
)
from app.modules.eval.assets import EvalAssets  # noqa: E402
from app.modules.eval.schemas import RunRequest  # noqa: E402
from app.modules.eval.service import EvalQuestion  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from tests.fakes import FakeConnector  # noqa: E402

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


def sql_reply(sql: str) -> str:
    """The SQL call's JSON: the SQL plus how to present it."""
    return json.dumps(
        {"sql": sql, "title": "在售商品", "chart": {"type": "table"}}, ensure_ascii=False
    )


ANSWER = "共 1 件在售商品，标价 10 元。"


def test_responses_artifact_maps_sql_and_keeps_answer_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: Any
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from run_pipeline import load_responses

    artifact = tmp_path / "responses.json"
    sql = "SELECT sku FROM commerce.product"
    artifact.write_text(
        json.dumps({"S01": [sql_reply(sql), json.dumps({"sql": sql}), ANSWER]}), encoding="utf-8"
    )
    [first, correction, answer] = load_responses(artifact, mapped)["S01"]
    mapped_sql = mapped.map_sql("commerce", sql)
    assert mapped_sql != sql
    assert json.loads(first) == {"sql": mapped_sql, "title": "在售商品", "chart": {"type": "table"}}
    assert json.loads(correction) == {"sql": mapped_sql}
    assert answer == ANSWER


async def test_replay_sql_reply_then_answer_text(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "S01")
    connector = FakeConnector([{"sku": "A1", "name": "杯子", "price": 10}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    sql = mapped.map_sql(case.datasource, "SELECT sku, name, price FROM commerce.product")
    scripts = {case.id: [sql_reply(sql), ANSWER]}
    adapter = build_simulated_adapter(mapped, scripts, executor, QueryLogWriter(Logs()))
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert outcome.status == "success" and outcome.executed_queries == 1
    assert outcome.retries == 0 and outcome.message == ANSWER
    assert outcome.chart_type == "table" and outcome.chart_valid


async def test_replay_correction_reply_comes_before_answer_text(mapped: Any) -> None:
    case = next(case for case in mapped.cases if case.id == "S01")
    connector = FakeConnector([{"sku": "A1", "name": "杯子", "price": 10}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    wrong = mapped.map_sql(case.datasource, "SELECT sku, colour FROM commerce.product")
    right = mapped.map_sql(case.datasource, "SELECT sku, name, price FROM commerce.product")
    # A made-up column is sent back to the model before anything runs.
    scripts = {case.id: [sql_reply(wrong), json.dumps({"sql": right}), ANSWER]}
    adapter = build_simulated_adapter(mapped, scripts, executor, QueryLogWriter(Logs()))
    outcome = await adapter.run(question(mapped, case), RunRequest(model_id="fixed"))
    assert outcome.status == "success" and outcome.executed_queries == 1
    assert outcome.retries == 1 and outcome.message == ANSWER


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
