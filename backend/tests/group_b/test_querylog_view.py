from datetime import UTC, datetime
from types import SimpleNamespace

from app.modules.querylog.service import QueryLogService


def test_view_explains_clarifications_and_rerun_origin_but_hides_raw_context():
    snapshot = {
        "clarification": {"question": "请选择指标", "choices": ["gmv", "net_sales"]},
        "rerun_of": {"question": "各季度销售额", "query_run_id": 7, "message_id": 3},
        "interpretation": "按季度汇总",
        "visible_schema": {"secret_table": {}},
        "history": [{"question": "earlier"}],
    }
    row = SimpleNamespace(
        id=8,
        created_at=datetime.now(UTC),
        payload={
            "user_id": 1,
            "team_id": None,
            "datasource_id": 1,
            "status": "clarify",
            "question": "订单",
            "context_snapshot": snapshot,
        },
    )
    view = QueryLogService.view(row).context_snapshot
    assert view["clarification"]["choices"] == ["gmv", "net_sales"]
    assert view["rerun_of"]["question"] == "各季度销售额" and view["interpretation"]
    assert "visible_schema" not in view and "history" not in view


async def test_cancelled_runs_filter_apart_from_real_failures(b_services, member, admin):
    from app.core.audit import QueryLogWriter
    from app.core.errors import ErrorCode
    from app.modules.querylog.schemas import QueryFilter, QueryMetadata
    from app.modules.querylog.service import _EnrichedStore

    service = b_services.querylog
    for error in (ErrorCode.QUERY_CANCELLED, ErrorCode.LLM_UNAVAILABLE):
        store = _EnrichedStore(service.repository, QueryMetadata(question=error.value), "failed")
        await QueryLogWriter(store).write(member, 1, "failed", details={"error": error.value})

    async def questions(status: str) -> list[str]:
        page = await service.list(admin, QueryFilter(status=status))  # type: ignore[arg-type]
        return [q.question for q in page.items]

    assert await questions("cancelled") == ["QUERY_CANCELLED"]
    assert await questions("failed") == ["LLM_UNAVAILABLE"]
