from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.datasource import ConnectorRegistry, Datasource
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.llm import LLMClient, ModelConfig, ModelRegistry
from app.core.sql.dialect import Dialect
from app.core.sql.executor import QueryResult, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.core.tasks import TaskManager, TaskStatus
from app.modules.eval.assets import EvalAssets
from app.modules.eval.gate1 import Gate1Runner
from app.modules.eval.repository import EvalRepository
from app.modules.eval.schemas import EvalCaseInput, PipelineOutcome, RunRequest
from app.modules.eval.scoring import compare_results, evaluate, summarize
from app.modules.eval.service import EvalService, GuardedReferenceRunner
from tests.fakes import (
    FakeConnector,
    FakeModels,
    FakeTransport,
    FakeUsage,
    MemoryQueue,
    MemoryTaskStore,
)
from tests.test_upload_d import d_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def result(columns, values, truncated=False):
    return QueryResult(
        columns=columns,
        rows=[dict(zip(columns, row, strict=True)) for row in values],
        row_count=len(values),
        duration_ms=0,
        truncated=truncated,
    )


def case(**changes):
    return EvalCaseInput.model_validate(
        {
            "id": "custom",
            "category": "single_table",
            "difficulty": "easy",
            "datasource": "commerce",
            "as_user": "sales_member",
            "question": "count orders",
            "expected_sql": "SELECT COUNT(*) AS n FROM commerce.orders",
            "result_mode": "scalar",
            **changes,
        }
    )


def test_compare_global_column_permutation_row_order_duplicates_and_tolerance():
    expected = result(["a", "b"], [["x", 1.0], ["y", 2.0]])
    actual = result(["renamed", "other"], [[2.0000009, "y"], [1.0000009, "x"]])
    assert compare_results(expected, actual)
    assert not compare_results(expected, actual, "ordered")
    assert not compare_results(expected, result(["x", "y"], [["x", 1], [2, "y"]]))
    assert not compare_results(result(["a"], [[1], [1]]), result(["b"], [[1], [2]]))
    assert not compare_results(result(["a"], [["00123"]]), result(["b"], [[123]]), "scalar")
    assert not compare_results(result(["a"], [[None]]), result(["b"], [[""]]))
    assert not compare_results(expected, expected.model_copy(update={"truncated": True}))
    assert not compare_results(expected, expected, "scalar")
    assert compare_results(
        result(["a"], [[0], [0.0000015]]), result(["b"], [[0.0000007], [-0.0000007]])
    )


@pytest.mark.parametrize(
    "change",
    [
        {"executed_queries": 1},
        {"retries": 1},
        {"message": "employee_salary blocked"},
        {"blocked_code": ErrorCode.SQL_NOT_SELECT},
        {"status": "success"},
    ],
)
def test_safety_requires_matching_code_zero_execution_and_no_leak(change):
    safety = case(
        category="out_of_scope",
        expect_status="blocked",
        expected_sql=None,
        expect_code=ErrorCode.SQL_OUT_OF_SCOPE,
    )
    outcome = PipelineOutcome(
        status="blocked",
        blocked_code=ErrorCode.SQL_OUT_OF_SCOPE,
        executed_queries=0,
        message="Out of scope",
    )
    assert evaluate(safety, outcome, hidden_names={"employee_salary"}).passed
    assert not evaluate(
        safety, outcome.model_copy(update=change), hidden_names={"employee_salary"}
    ).passed


def test_clarification_and_auxiliary_scoring():
    clarify = case(
        category="clarify",
        expect_status="clarify",
        expected_sql=None,
        expected_clarification_options=["原价", "应收"],
    )
    assert evaluate(
        clarify,
        PipelineOutcome(
            status="clarify", executed_queries=0, clarification_options=["原价", "应收", "实收"]
        ),
    ).passed
    assert not evaluate(clarify, PipelineOutcome(status="success", executed_queries=1)).passed
    chart = case(category="chart", expected_chart_types=["bar"])
    data = result(["n"], [[1]])
    assert not evaluate(
        chart, PipelineOutcome(status="success", executed_queries=1, result=data), data
    ).passed
    assert evaluate(
        chart,
        PipelineOutcome(
            status="success", executed_queries=1, result=data, chart_type="bar", chart_valid=True
        ),
        data,
    ).passed
    assert not evaluate(
        case(),
        PipelineOutcome(status="success", executed_queries=1, result=result(["n"], [[None]])),
        result(["n"], [[None]]),
    ).passed


async def test_gate1_real_assets_fixed_model_real_sql_guard():
    assets = EvalAssets(ROOT / "eval")
    usage = FakeUsage()
    models = ModelRegistry(
        FakeModels(
            [
                ModelConfig(id="fixed", kind="llm", model="fake", api_key=SecretStr("fake")),
            ]
        )
    )
    runner = Gate1Runner(
        assets, lambda sql: LLMClient(models, usage, FakeTransport(sql)), SqlGuard()
    )
    results, summary = await runner.run()
    assert len(assets.cases) == 100 and len(results) == 22
    assert all(r.passed for r in results)
    assert summary["safety_gate_passed"] and summary["blocked_pass_rate"] == 1
    assert len(usage.records) == 22
    scope = await assets.permissions.visible_scope(assets.users["sales_member"], 1)
    assert "commerce.employee_salary" not in scope.tables
    assert not summarize(results[:1])["safety_gate_passed"]


async def test_reference_runner_uses_permission_guard_executor_only():
    assets = EvalAssets(ROOT / "eval")
    connector = FakeConnector([{"n": 1}])
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    datasource = Datasource(
        id=1,
        dialect=Dialect.POSTGRES,
        host="fake",
        port=5432,
        database="test",
        username="reader",
        password=SecretStr("fake"),
    )

    class Provider:
        async def resolve(self, name):
            return datasource

    runner = GuardedReferenceRunner(assets, Provider(), assets.permissions, SqlGuard(), executor)
    assert (await runner.result(case())).rows == [{"n": 1}]
    assert connector.executed[0].startswith('SELECT COUNT(*) AS "n"')
    assert "commerce" in connector.executed[0]
    with pytest.raises(AppError):
        await runner.result(case(expected_sql="SELECT cost FROM commerce.product"))
    with pytest.raises(AppError):
        await runner.result(
            case(expect_status="blocked", expected_sql="DELETE FROM commerce.orders")
        )
    assert len(connector.executed) == 1


async def test_background_run_snapshot_metrics_and_comparison(d_database):  # noqa: F811
    assets = EvalAssets(ROOT / "eval")
    store = MemoryTaskStore()
    tasks = TaskManager(store, MemoryQueue(), {})
    admin = CurrentUser(id=1, role=Role.ADMIN)
    data = result(["count"], [[3]])

    class Pipeline:
        wrong = False
        seen = []

        async def run(self, question, config):
            self.seen.append(question.model_dump())
            assert "expected_sql" not in question.model_dump()
            return PipelineOutcome(
                status="success",
                executed_queries=1,
                result=result(["n"], [[4]]) if self.wrong else data,
                retries=1 if self.wrong else 0,
                duration_ms=10,
                tokens_in=7,
            )

    class Reference:
        async def result(self, question):
            return data

    pipeline = Pipeline()
    async with d_database.session() as session:
        service = EvalService(
            EvalRepository(session),
            assets,
            pipeline,
            Reference(),
            AuditService(DatabaseAuditStore(session)),
            tasks,
        )
        tasks.handlers["eval.run"] = service.run
        await service.save_case(case(), actor=admin)
        first = await service.start(RunRequest(model_id="fixed"), actor=admin)
        # Editing the case cannot alter a queued run's immutable definition snapshot.
        await service.save_case(case(question="edited"), actor=admin)
        await tasks.run(first.task_id)
        await tasks.run(first.task_id)
        assert store.records[first.task_id].status == TaskStatus.SUCCESS
        summary = (await service.get(first.id, actor=admin)).summary
        assert summary["pass_rate"] == summary["first_pass_rate"] == 1
        assert summary["tokens_in"] == 7 and summary["average_duration_ms"] == 10
        assert pipeline.seen[0]["question"] == "count orders"
        pipeline.wrong = True
        second = await service.start(RunRequest(model_id="fixed"), actor=admin)
        await tasks.run(second.task_id)
        compare = await service.compare(first.id, second.id, actor=admin)
        assert compare.new_failures == ["custom"] and compare.fixed == []
        assert (await service.compare(second.id, first.id, actor=admin)).fixed == ["custom"]
        with pytest.raises(AppError):
            await service.start(
                RunRequest(model_id="fixed"), actor=CurrentUser(id=2, role=Role.LEADER, team_id=1)
            )
        with pytest.raises(AppError):
            await service.get(first.id, actor=CurrentUser(id=3, role=Role.MEMBER, team_id=1))


def test_module_routers_generate_openapi_without_binding_chat():
    from fastapi import FastAPI

    from app.modules.eval.api import router as eval_router
    from app.modules.upload.api import router as upload_router

    app = FastAPI()
    app.include_router(upload_router, prefix="/api")
    app.include_router(eval_router, prefix="/api")
    schema = app.openapi()
    assert "/api/eval/runs" in schema["paths"] and "/api/upload/preview" in schema["paths"]
    assert "EvalCaseInput" in schema["components"]["schemas"]
