from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError, ErrorCode
from app.core.sql.executor import QueryResult
from app.modules.querylog.schemas import QueryFilter, QueryMetadata


async def test_roles_pagination_filters_and_blocked_redaction(
    b_services, member, leader, admin, outsider
):
    ids = []
    for actor in [member, leader, outsider]:
        ids.append(
            await b_services.querylog.write(
                actor,
                1,
                "blocked",
                blocked_code=ErrorCode.SQL_OUT_OF_SCOPE,
                metadata=QueryMetadata(
                    question="request",
                    generated_sql="SELECT salary FROM hidden_payroll",
                    context_snapshot={"metrics": ["hidden_payroll"]},
                ),
            )
        )
    page = await b_services.querylog.list(admin, QueryFilter(status="blocked", page_size=2))
    assert page.total == 3 and len(page.items) == 2
    assert (await b_services.querylog.list(leader, QueryFilter())).total == 2
    assert (await b_services.querylog.list(member, QueryFilter())).total == 1
    assert (await b_services.querylog.list(member, QueryFilter(user_id=outsider.id))).total == 0
    assert (
        await b_services.querylog.list(admin, QueryFilter(blocked_code="SQL_OUT_OF_SCOPE"))
    ).total == 3
    for row in page.items:
        assert row.generated_sql is None and row.normalized_sql is None
        assert row.context_snapshot == {} and row.result_preview == []
        assert "hidden_payroll" not in row.model_dump_json()
    with pytest.raises(AppError):
        await b_services.querylog.get(ids[-1], member)
    await b_services.querylog.mark_feedback(ids[0], actor=member)
    assert (await b_services.querylog.list(admin, QueryFilter(has_feedback=True))).total == 1
    assert (await b_services.querylog.list(admin, QueryFilter(has_feedback=False))).total == 2
    future = datetime.now(UTC) + timedelta(days=1)
    assert (await b_services.querylog.list(admin, QueryFilter(since=future))).total == 0


async def test_core_writer_masks_and_bounds_preview(b_services, member):
    scope = await b_services.permissions.visible_scope(member, 1)
    from app.core.masking import MaskKind, MaskRule

    scope.tables["orders"].masks = {"phone": MaskRule(kind=MaskKind.PHONE)}
    sql = b_services.metric.guard.check("SELECT phone FROM orders", "postgres", scope)
    result = QueryResult(
        columns=["phone"],
        rows=[{"phone": "13812345678"}] * 50,
        row_count=50,
        truncated=False,
        duration_ms=3,
    )
    ref = await b_services.querylog.write(
        member,
        1,
        "success",
        sql=sql,
        result=result,
        metadata=QueryMetadata(
            question="phones",
            context_snapshot={
                "metrics": [{"id": 1, "version": 2}],
                "full_result": result.rows,
                "api_key": "never-save",
                "prompt_versions": {"sql": "v1"},
            },
        ),
    )
    query = await b_services.querylog.get(ref, member)
    assert len(query.result_preview) == 5
    assert query.result_preview[0]["phone"] == "138****5678"
    assert query.context_snapshot == {
        "metrics": [{"id": 1, "version": 2}],
        "prompt_versions": {"sql": "v1"},
    }
