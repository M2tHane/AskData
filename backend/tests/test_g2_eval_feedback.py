from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog, AuditRecord, AuditService, QueryRun
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.retention import DatabaseRetentionStore
from app.core.sql.executor import QueryResult
from app.core.sql.guard import SqlGuard
from app.core.tasks import TaskManager
from app.modules.eval.assets import EvalAssets
from app.modules.eval.models import EvalCase
from app.modules.eval.repository import EvalRepository
from app.modules.eval.schemas import EvalCaseInput
from app.modules.eval.service import (
    EvalService,
    GuardedReferenceRunner,
    build_feedback_sink,
    case_clock,
)
from app.modules.feedback.models import Feedback
from app.modules.feedback.schemas import ConversionInput, FeedbackInput
from app.modules.feedback.service import EvaluationSink
from app.modules.querylog.schemas import QueryMetadata
from tests.fakes import MemoryQueue, MemoryTaskStore

ROOT = Path(__file__).resolve().parents[2]
pytest_plugins = ["tests.group_b.conftest"]
MEMBER = CurrentUser(id=3, role=Role.MEMBER, team_id=1)
LEADER = CurrentUser(id=2, role=Role.LEADER, team_id=1)
ADMIN = CurrentUser(id=1, role=Role.ADMIN)


@pytest.fixture
def assets(b_services: SimpleNamespace) -> EvalAssets:
    assets = EvalAssets(ROOT / "eval")
    assets.permissions = b_services.permissions
    return assets


async def submitted(services: SimpleNamespace) -> Any:
    sql = services.metric.guard.check(
        "SELECT id FROM orders", "postgres", await services.permissions.visible_scope(MEMBER, 1)
    )
    query_id = await services.querylog.write(
        MEMBER,
        1,
        "success",
        sql=sql,
        result=QueryResult(
            columns=["id"], rows=[{"id": 1}], row_count=1, duration_ms=0, truncated=False
        ),
        metadata=QueryMetadata(
            question="orders",
            context_snapshot={
                "time_range": {"timezone": "Europe/Paris"},
                "opaque_private_data": "not-for-eval-copy",
            },
        ),
    )
    return await services.feedback.submit(
        FeedbackInput(query_run_id=query_id, rating="down", reason_code="result_wrong"),
        actor=MEMBER,
    )


async def case_row(services: SimpleNamespace, identifier: int) -> EvalCase:
    row = await services.session.get(EvalCase, identifier)
    assert row is not None
    return row


async def test_real_feedback_sink_persists_expectations_and_survives_snapshot_retention(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    sink: EvaluationSink = build_feedback_sink(b_services.session, assets, b_services.permissions)
    b_services.feedback.evaluations = sink
    feedback = await submitted(b_services)
    converted = await b_services.feedback.convert(
        feedback.id,
        ConversionInput(kind="eval", expected={"rows": [{"id": 1}], "result_mode": "scalar"}),
        actor=LEADER,
    )
    assert converted.converted_ref is not None
    identifier = int(converted.converted_ref.split(":", 1)[1])
    row = await case_row(b_services, identifier)
    definition = EvalCaseInput.model_validate(row.definition)
    assert row.source == definition.source == "feedback"
    assert definition.as_user == "sales_member" and definition.datasource == "commerce"
    assert definition.question == "orders" and definition.expected_values == [[1]]
    assert definition.expected_columns == ["id"] and row.created_by == LEADER.id
    assert definition.expected_sql is None  # Never use the old model query as a reference answer.
    assert definition.feedback_origin is not None
    assert definition.feedback_origin.query_run_id == feedback.query_run_id
    assert definition.feedback_origin.converted_by == LEADER.id
    assert definition.feedback_origin.timezone == "Europe/Paris"
    clock = case_clock(definition, assets.fixtures["clock"])
    assert clock["timezone"] == "Europe/Paris"
    assert clock["now"] != assets.fixtures["clock"]["now"]
    assert "not-for-eval-copy" not in str(row.definition)
    audits = list(await b_services.session.scalars(select(AuditLog)))
    conversion_audits = [a for a in audits if a.payload["action"] == "eval.case.from_feedback"]
    assert len(conversion_audits) == 1 and conversion_audits[0].payload["actor_id"] == LEADER.id
    assert "rows" not in conversion_audits[0].payload["diff"]
    assert "expected_values" not in conversion_audits[0].payload["diff"]
    with pytest.raises(AppError) as duplicate:
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": [{"id": 1}]}), actor=LEADER
        )
    assert duplicate.value.code == ErrorCode.CONFLICT
    await DatabaseRetentionStore(
        b_services.session, [Feedback.__table__, QueryRun.__table__]
    ).purge(datetime.now(UTC) + timedelta(days=91))
    assert not list(await b_services.session.scalars(select(Feedback)))
    assert not list(await b_services.session.scalars(select(QueryRun)))
    assert (await case_row(b_services, identifier)).expected_result == [[1]]


@pytest.mark.parametrize(
    "actor",
    [
        MEMBER,
        CurrentUser(id=4, role=Role.LEADER, team_id=2),
        LEADER.model_copy(update={"active": False}),
        LEADER.model_copy(update={"team_id": None}),
    ],
)
async def test_sink_roles_are_checked_even_when_called_without_feedback_service(
    b_services: SimpleNamespace,
    assets: EvalAssets,
    actor: CurrentUser,
) -> None:
    feedback = await submitted(b_services)
    with pytest.raises(AppError) as error:
        await build_feedback_sink(b_services.session, assets).create_case_from_feedback(
            feedback.snapshot, {"rows": [{"id": 1}]}, actor=actor
        )
    assert error.value.code == ErrorCode.FORBIDDEN
    assert not list(await b_services.session.scalars(select(EvalCase)))


@pytest.mark.parametrize(
    "expected",
    [
        {},
        {"id": "V01", "rows": [{"id": 1}]},
        {"source": "manual", "rows": [{"id": 1}]},
        {"as_user": "admin", "rows": [{"id": 1}]},
        {"datasource": "upload_budget", "rows": [{"id": 1}]},
        {"rows": [{"a": 1}, {"b": 2}]},
        {"expected_values": [[1], [1, 2]]},
        {"expected_values": [[1, 2]], "result_mode": "scalar"},
        {"rows": [{"id": 1}], "expected_values": [[1]]},
        {"rows": [{"id": float("nan")}]},
        {"rows": []},
        {"rows": [], "expect_empty": True},
        {
            "expect_status": "blocked",
            "expect_code": "SQL_OUT_OF_SCOPE",
            "expected_sql": "SELECT id FROM orders",
        },
        {"expect_status": "blocked", "expect_code": "SQL_OUT_OF_SCOPE"},
        {"expect_status": "clarify", "expected_clarification_options": ["single"]},
        {"rows": [{"id": 1}], "category": "chart"},
    ],
)
async def test_incomplete_or_forged_expectations_never_enter_suite(
    b_services: SimpleNamespace,
    assets: EvalAssets,
    expected: dict[str, Any],
) -> None:
    feedback = await submitted(b_services)
    with pytest.raises(AppError) as error:
        await build_feedback_sink(b_services.session, assets).create_from_feedback(
            feedback.snapshot, expected, actor=LEADER
        )
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert not list(await b_services.session.scalars(select(EvalCase)))


async def test_corrected_reference_sql_is_guarded_as_original_employee_not_leader(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)
    b_services.provider.grants[0].team_columns |= frozenset({"secret"})
    sink = build_feedback_sink(b_services.session, assets)
    for sql, expected_code in [
        ("DELETE FROM orders", ErrorCode.SQL_NOT_SELECT),
        ("SELECT secret FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
    ]:
        with pytest.raises(AppError) as error:
            await sink.create_case_from_feedback(
                feedback.snapshot, {"expected_sql": sql}, actor=LEADER
            )
        assert error.value.code == expected_code and "secret" not in error.value.message
    identifier = await sink.create_case_from_feedback(
        feedback.snapshot, {"expected_sql": "SELECT id FROM orders"}, actor=LEADER
    )
    row = await case_row(b_services, identifier)
    assert row.expected_sql is not None and '"id"' in row.expected_sql
    assert b_services.connector.opened == 0 and not b_services.connector.executed


async def test_original_identity_bindings_and_live_revocations_fail_closed(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)
    sink = build_feedback_sink(b_services.session, assets)
    for field, value in [("datasource_id", 1234), ("user_id", 1234), ("team_id", 2)]:
        with pytest.raises(AppError):
            await sink.create_from_feedback(
                {**feedback.snapshot, field: value}, {"rows": [{"id": 1}]}, actor=LEADER
            )
    assets.users["sales_member"] = assets.users["sales_member"].model_copy(update={"active": False})
    with pytest.raises(AppError):
        await sink.create_from_feedback(feedback.snapshot, {"rows": [{"id": 1}]}, actor=ADMIN)
    assets.users["sales_member"] = MEMBER
    b_services.provider.grants.clear()
    with pytest.raises(AppError) as error:
        await sink.create_from_feedback(feedback.snapshot, {"rows": [{"id": 1}]}, actor=LEADER)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert not list(await b_services.session.scalars(select(EvalCase)))


async def test_audit_failure_rolls_back_eval_copy_and_feedback_conversion_together(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)

    class FailingAudit:
        async def append(self, record: AuditRecord) -> None:
            await b_services.audit.store.append(record)
            raise AppError(ErrorCode.TASK_FAILED)

    b_services.feedback.evaluations = build_feedback_sink(
        b_services.session, assets, audit=AuditService(FailingAudit())
    )
    with pytest.raises(AppError):
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": [{"id": 1}]}), actor=LEADER
        )
    assert not list(await b_services.session.scalars(select(EvalCase)))
    stored_feedback = await b_services.session.get(Feedback, feedback.id)
    assert stored_feedback is not None and stored_feedback.converted_ref is None
    assert stored_feedback.status == "open"
    audits = list(await b_services.session.scalars(select(AuditLog)))
    assert not any(record.payload["action"] == "eval.case.from_feedback" for record in audits)


async def test_values_only_reference_requires_no_sql_and_explicit_empty_width(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)
    sink = build_feedback_sink(b_services.session, assets)
    populated = await sink.create_case_from_feedback(
        feedback.snapshot, {"rows": [{"id": 1}], "result_mode": "scalar"}, actor=LEADER
    )
    empty = await sink.create_case_from_feedback(
        feedback.snapshot,
        {"category": "empty", "rows": [], "expected_columns": ["id"], "expect_empty": True},
        actor=LEADER,
    )

    class Provider:
        async def resolve(self, name: str) -> Any:
            return await b_services.metric.datasources.resolve(1, MEMBER)

    runner = GuardedReferenceRunner(
        assets, Provider(), assets.permissions, SqlGuard(), b_services.metric.executor
    )
    data = await runner.result(
        EvalCaseInput.model_validate((await case_row(b_services, populated)).definition)
    )
    assert data.rows == [{"id": 1}]
    data = await runner.result(
        EvalCaseInput.model_validate((await case_row(b_services, empty)).definition)
    )
    assert data.columns == ["id"] and not data.rows
    assert not b_services.connector.opened


async def test_reference_sql_and_explicit_values_must_agree(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)
    identifier = await build_feedback_sink(b_services.session, assets).create_case_from_feedback(
        feedback.snapshot,
        {"expected_sql": "SELECT id FROM orders", "rows": [{"id": 2}]},
        actor=LEADER,
    )

    class Provider:
        async def resolve(self, name: str) -> Any:
            return await b_services.metric.datasources.resolve(1, MEMBER)

    runner = GuardedReferenceRunner(
        assets, Provider(), assets.permissions, SqlGuard(), b_services.metric.executor
    )
    with pytest.raises(AppError) as error:
        await runner.result(
            EvalCaseInput.model_validate((await case_row(b_services, identifier)).definition)
        )
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert len(b_services.connector.executed) == 1


async def test_eval_service_public_method_keeps_general_maintenance_admin_only(
    b_services: SimpleNamespace,
    assets: EvalAssets,
) -> None:
    feedback = await submitted(b_services)

    class Unused:
        async def run(self, question: Any, config: Any) -> Any:
            raise AssertionError("conversion must not call the model")

        async def result(self, case: Any) -> QueryResult:
            raise AssertionError("conversion must not execute a reference")

    service = EvalService(
        EvalRepository(b_services.session),
        assets,
        Unused(),
        Unused(),
        b_services.audit,
        TaskManager(MemoryTaskStore(), MemoryQueue(), {}),
    )
    identifier = await service.create_case_from_feedback(
        feedback.snapshot, {"rows": [{"id": 1}]}, actor=LEADER
    )
    case = EvalCaseInput.model_validate((await case_row(b_services, identifier)).definition)
    with pytest.raises(AppError):
        await service.save_case(case, actor=LEADER)
    assert len(await service.repository.all_cases()) == 1 and len(assets.cases) == 100
