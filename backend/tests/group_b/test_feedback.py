import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.audit import QueryRun
from app.core.errors import AppError, ErrorCode
from app.core.response import PageParams
from app.core.retention import DatabaseRetentionStore
from app.core.sql.executor import QueryResult
from app.modules.feedback.models import Feedback
from app.modules.feedback.schemas import ConversionInput, ExampleInput, ExampleReview, FeedbackInput
from app.modules.querylog.schemas import QueryMetadata


async def answered(b_services, member):
    sql = b_services.metric.guard.check(
        "SELECT id FROM orders", "postgres", await b_services.permissions.visible_scope(member, 1)
    )
    return await b_services.querylog.write(
        member,
        1,
        "success",
        sql=sql,
        result=QueryResult(
            columns=["id"], rows=[{"id": 1}], row_count=1, truncated=False, duration_ms=2
        ),
        metadata=QueryMetadata(
            question="orders",
            model_id="fake",
            context_snapshot={
                "metrics": [{"id": 12, "version": 2}],
                "prompt_versions": {"sql": "v1"},
            },
        ),
    )


def test_downvote_requires_registered_reason():
    with pytest.raises(AppError):
        FeedbackInput(query_run_id=1, rating="down")
    with pytest.raises(ValidationError):
        FeedbackInput(query_run_id=1, rating="down", reason_code="unregistered")


async def test_feedback_snapshot_and_roles(b_services, member, leader, admin, outsider):
    identifier = await answered(b_services, member)
    data = FeedbackInput(query_run_id=identifier, rating="down", reason_code="metric_wrong")
    for actor in (leader, admin, outsider):
        with pytest.raises(AppError):
            await b_services.feedback.submit(data, actor=actor)
    feedback = await b_services.feedback.submit(data, actor=member)
    assert feedback.snapshot["context_snapshot"]["metrics"][0]["version"] == 2
    assert feedback.snapshot["question"] == "orders"
    assert (await b_services.feedback.list(leader, PageParams())).total == 1
    assert (await b_services.feedback.list(outsider, PageParams())).total == 0
    for actor in (member, outsider):
        with pytest.raises(AppError):
            await b_services.feedback.handle(feedback.id, actor=actor)
    handled = await b_services.feedback.handle(feedback.id, actor=leader)
    assert handled.status == "handled" and handled.handled_by == leader.id


async def test_conversion_requires_example_review_and_live_guard(b_services, member, leader):
    identifier = await answered(b_services, member)
    feedback = await b_services.feedback.submit(
        FeedbackInput(query_run_id=identifier, rating="up"), actor=member
    )
    with pytest.raises(AppError):
        await b_services.feedback.convert(
            feedback.id,
            ConversionInput(kind="example", corrected_sql="DELETE FROM orders"),
            actor=leader,
        )
    converted = await b_services.feedback.convert(
        feedback.id, ConversionInput(kind="example"), actor=leader
    )
    example_id = int(converted.converted_ref.split(":")[1])
    assert await b_services.feedback.examples(member, 1) == []
    pending = await b_services.feedback.examples(leader, 1, approved_only=False)
    assert pending[0].id == example_id and not pending[0].approved
    with pytest.raises(AppError):
        await b_services.feedback.review_example(
            example_id, ExampleReview(approved=True), actor=member
        )
    await b_services.feedback.review_example(example_id, ExampleReview(approved=True), actor=leader)
    assert (await b_services.feedback.examples(member, 1))[0].approved
    assert b_services.index.events[-1] == ("example", example_id, 1)
    b_services.provider.grants[0].member_columns = frozenset({"phone"})
    assert await b_services.feedback.examples(member, 1) == []
    with pytest.raises(AppError):
        await b_services.feedback.convert(
            feedback.id, ConversionInput(kind="example"), actor=leader
        )


async def test_eval_copy_survives_snapshot_retention(b_services, member, leader):
    identifier = await answered(b_services, member)
    feedback = await b_services.feedback.submit(
        FeedbackInput(query_run_id=identifier, rating="down", reason_code="result_wrong"),
        actor=member,
    )
    converted = await b_services.feedback.convert(
        feedback.id, ConversionInput(kind="eval", expected={"rows": [{"id": 1}]}), actor=leader
    )
    assert converted.converted_ref == "eval:1"
    from datetime import UTC, datetime, timedelta

    await DatabaseRetentionStore(
        b_services.session, [Feedback.__table__, QueryRun.__table__]
    ).purge(datetime.now(UTC) + timedelta(days=91))
    assert not list(await b_services.session.scalars(select(Feedback)))
    assert b_services.evaluations.cases[0][0]["question"] == "orders"


async def test_manual_example_scope_and_sql_validation(b_services, member, leader):
    with pytest.raises(AppError):
        await b_services.feedback.create_example(
            ExampleInput(datasource_id=1, question="test", sql="SELECT id FROM orders"),
            actor=member,
        )
    with pytest.raises(AppError) as error:
        await b_services.feedback.create_example(
            ExampleInput(datasource_id=1, question="test", sql="SELECT secret FROM orders"),
            actor=leader,
        )
    assert error.value.code == ErrorCode.SQL_OUT_OF_SCOPE
