from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.audit import AuditLog, AuditService, DatabaseAuditStore, QueryRun
from app.core.errors import AppError, ErrorCode
from app.core.retention import CompositeRetentionStore, DatabaseRetentionStore
from app.modules.eval.models import EvalCase
from app.modules.feedback.models import Feedback
from app.modules.feedback.schemas import ConversionInput, FeedbackInput
from app.modules.feedback.service import EvaluationSink, build_retention_store
from tests.group_b.test_feedback import answered


class FakeEvaluationSink:
    """Test-only independent draft persistence, sharing the feedback transaction."""

    def __init__(self, session):
        self.session = session
        self.calls = []
        self.fail = False

    async def create_from_feedback(self, snapshot, expected, *, actor):
        self.calls.append((deepcopy(snapshot), deepcopy(expected), actor))
        row = EvalCase(
            external_id=f"fake-feedback-{uuid4()}",
            category="single_table",
            difficulty="easy",
            question=snapshot["question"],
            expected_sql=snapshot["normalized_sql"],
            expected_result=None,
            expect_blocked=False,
            expected_code=None,
            source="feedback",
            definition={
                "draft": True,
                "snapshot": deepcopy(snapshot),
                "expected": deepcopy(expected),
            },
            created_by=actor.id,
        )
        self.session.add(row)
        await self.session.flush()
        await AuditService(DatabaseAuditStore(self.session)).record(
            actor, "eval.fake_create", ("eval_case", str(row.id))
        )
        if self.fail:
            raise AppError(ErrorCode.TASK_FAILED)
        # A sink must not be able to mutate the original stored feedback or request.
        snapshot["question"] = "mutated by fake sink"
        expected.clear()
        return row.id


async def submitted(b_services, member):
    return await b_services.feedback.submit(
        FeedbackInput(query_run_id=await answered(b_services, member), rating="up"), actor=member
    )


@pytest.mark.parametrize("as_admin", [False, True])
@pytest.mark.parametrize("logs_first", [False, True])
async def test_conversion_is_atomic_independent_and_survives_public_composite_retention(
    b_services, member, leader, admin, as_admin, logs_first
):
    await b_services.session.execute(text("PRAGMA foreign_keys = ON"))
    sink: EvaluationSink = FakeEvaluationSink(b_services.session)
    b_services.feedback.evaluations = sink
    feedback = await submitted(b_services, member)
    expected = ConversionInput(kind="eval", expected={"rows": [{"id": 1}]})
    actor = admin if as_admin else leader
    converted = await b_services.feedback.convert(feedback.id, expected, actor=actor)
    case_id = int(converted.converted_ref.split(":")[1])
    case = await b_services.session.get(EvalCase, case_id)
    assert case.created_by == actor.id and case.source == "feedback"
    assert case.definition["draft"]
    assert case.definition["snapshot"]["context_snapshot"]["metrics"][0]["version"] == 2
    assert case.definition["expected"] == {"rows": [{"id": 1}]}
    assert converted.snapshot["question"] == "orders" and expected.expected["rows"] == [{"id": 1}]
    assert converted.status == "handled" and converted.handled_by == actor.id
    with pytest.raises(AppError) as caught:
        await b_services.feedback.convert(feedback.id, expected, actor=actor)
    assert caught.value.code == ErrorCode.CONFLICT
    assert len(sink.calls) == 1
    actions = [row.payload for row in await b_services.session.scalars(select(AuditLog))]
    assert {row["action"] for row in actions} >= {"feedback.convert", "eval.fake_create"}
    assert all(
        row["actor_id"] == actor.id
        for row in actions
        if row["action"] in {"feedback.convert", "eval.fake_create"}
    )
    # A newer feedback/log must remain after the older pair is removed.
    live = await submitted(b_services, member)
    now = datetime.now(UTC)
    old_query = await b_services.session.get(QueryRun, feedback.query_run_id)
    old_query.expires_at = now
    old_feedback = await b_services.session.get(Feedback, feedback.id)
    old_feedback.expires_at = now
    await b_services.session.flush()
    cleaners = [
        build_retention_store(b_services.session),
        DatabaseRetentionStore(b_services.session, [QueryRun.__table__]),
    ]
    if logs_first:
        cleaners.reverse()
    result = await CompositeRetentionStore(cleaners).purge(now)
    assert result["query_run"] == 1
    assert result["feedback"] == (0 if logs_first else 1)  # FK cascades are not double-counted.
    assert list(await b_services.session.scalars(select(Feedback.id))) == [live.id]
    assert list(await b_services.session.scalars(select(QueryRun.id))) == [live.query_run_id]
    await b_services.session.refresh(case)
    assert case.definition["snapshot"]["question"] == "orders"
    assert await build_retention_store(b_services.session).purge(now) == {"feedback": 0}


async def test_conversion_sink_failure_rolls_back_draft_marker_and_both_audits(
    b_services, member, leader
):
    sink = FakeEvaluationSink(b_services.session)
    sink.fail = True
    b_services.feedback.evaluations = sink
    feedback = await submitted(b_services, member)
    previous = await b_services.session.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(AppError) as caught:
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": []}), actor=leader
        )
    assert caught.value.code == ErrorCode.TASK_FAILED
    assert await b_services.session.scalar(select(func.count()).select_from(EvalCase)) == 0
    assert await b_services.session.scalar(select(func.count()).select_from(AuditLog)) == previous
    row = await b_services.session.get(Feedback, feedback.id)
    await b_services.session.refresh(row)
    assert row.converted_ref is None and row.handled_by is None and row.status == "open"
    sink.fail = False
    assert (
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": []}), actor=leader
        )
    ).converted_ref.startswith("eval:")


async def test_eval_conversion_denies_wrong_actor_missing_sink_and_missing_expected(
    b_services, member, leader, outsider, admin
):
    feedback = await submitted(b_services, member)
    data = ConversionInput(kind="eval", expected={"rows": []})
    sink = FakeEvaluationSink(b_services.session)
    b_services.feedback.evaluations = sink
    for actor in (member, outsider, leader.model_copy(update={"active": False})):
        with pytest.raises(AppError) as caught:
            await b_services.feedback.convert(feedback.id, data, actor=actor)
        assert caught.value.code == ErrorCode.FORBIDDEN
    assert not sink.calls
    with pytest.raises(AppError) as caught:
        await b_services.feedback.convert(feedback.id, ConversionInput(kind="eval"), actor=admin)
    assert caught.value.code == ErrorCode.VALIDATION_ERROR and not sink.calls
    b_services.feedback.evaluations = None
    with pytest.raises(AppError) as caught:
        await b_services.feedback.convert(feedback.id, data, actor=leader)
    assert caught.value.code == ErrorCode.TASK_FAILED and caught.value.http_status == 503


async def test_retention_drops_example_source_snapshot_but_keeps_example(
    b_services, member, leader
):
    feedback = await submitted(b_services, member)
    converted = await b_services.feedback.convert(
        feedback.id, ConversionInput(kind="example"), actor=leader
    )
    example_id = int(converted.converted_ref.split(":")[1])
    now = datetime.now(UTC) + timedelta(days=91)
    assert await build_retention_store(b_services.session).purge(now) == {"feedback": 1}
    assert await b_services.feedback.repository.examples.get(example_id) is not None


async def test_feedback_audit_failure_also_rolls_back_successful_sink(b_services, member, leader):
    feedback = await submitted(b_services, member)
    b_services.feedback.evaluations = FakeEvaluationSink(b_services.session)

    class FailingAudit:
        async def record(self, *args, **kwargs):
            raise AppError(ErrorCode.INTERNAL_ERROR)

    b_services.feedback.audit = FailingAudit()
    with pytest.raises(AppError):
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": []}), actor=leader
        )
    assert await b_services.session.scalar(select(func.count()).select_from(EvalCase)) == 0
    row = await b_services.session.get(Feedback, feedback.id)
    await b_services.session.refresh(row)
    assert row.converted_ref is None and row.status == "open"
    actions = [row.payload["action"] for row in await b_services.session.scalars(select(AuditLog))]
    assert "eval.fake_create" not in actions and "feedback.convert" not in actions


@pytest.mark.parametrize("receipt", [0, -1, True])
async def test_invalid_sink_receipt_does_not_mark_converted(b_services, member, leader, receipt):
    feedback = await submitted(b_services, member)

    class BadSink:
        async def create_from_feedback(self, snapshot, expected, *, actor):
            return receipt

    b_services.feedback.evaluations = BadSink()
    with pytest.raises(AppError) as caught:
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="eval", expected={"rows": []}), actor=leader
        )
    assert caught.value.code == ErrorCode.TASK_FAILED
    row = await b_services.session.get(Feedback, feedback.id)
    assert row.converted_ref is None
