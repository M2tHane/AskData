import pytest
from sqlalchemy import func, select

from app.core.errors import AppError, ErrorCode
from app.modules.metric.models import Metric
from app.modules.metric.schemas import MetricInput
from app.modules.metric.service import render_template


async def test_save_trial_normalized_bounded_sql(b_services, member):
    data = MetricInput(
        datasource_id=1,
        name="order",
        sql_template="SELECT id FROM orders WHERE id > :minimum -- raw",
        params={"minimum": 0},
    )
    item = await b_services.metric.save(data, actor=member)
    assert item.last_validated_at and item.version == 1
    executed = b_services.connector.executed[0]
    assert "LIMIT 100" in executed and "raw" not in executed and ":minimum" not in executed
    assert executed == b_services.connector.explained[0]
    item = await b_services.metric.save(data, identifier=item.id, actor=member)
    assert item.version == 2 and len(b_services.connector.executed) == 2


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DELETE FROM orders", ErrorCode.SQL_NOT_SELECT),
        ("SELECT id FROM hidden_payroll", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT secret FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders; DROP TABLE orders", ErrorCode.SQL_MULTI_STATEMENT),
    ],
)
async def test_guard_rejection_never_executes_or_saves(b_services, member, sql, code):
    with pytest.raises(AppError) as error:
        await b_services.metric.save(
            MetricInput(datasource_id=1, name="bad", sql_template=sql), actor=member
        )
    assert error.value.code == code
    assert not b_services.connector.executed and b_services.connector.opened == 0
    assert await b_services.session.scalar(select(func.count()).select_from(Metric)) == 0


async def test_executor_failure_does_not_save(b_services, member):
    b_services.connector.error = RuntimeError("driver credentials hidden object")
    with pytest.raises(AppError) as error:
        await b_services.metric.save(
            MetricInput(datasource_id=1, name="bad", sql_template="SELECT id FROM orders"),
            actor=member,
        )
    assert error.value.code == ErrorCode.DATASOURCE_UNAVAILABLE
    assert await b_services.session.scalar(select(func.count()).select_from(Metric)) == 0


@pytest.mark.parametrize("value", ["x'; DROP TABLE orders; --", "paid's status", 12, True, None])
async def test_parameters_are_literals(b_services, member, value):
    rendered = render_template("SELECT id FROM orders WHERE phone = :value", {"value": value})
    scope = await b_services.permissions.visible_scope(member, 1)
    assert b_services.metric.guard.check(rendered, "postgres", scope).authentic()


@pytest.mark.parametrize(
    "params", [{}, {"value": float("nan")}, {"value": "x\\'"}, {"value": "\x00"}]
)
def test_invalid_template_parameters(params):
    with pytest.raises(AppError):
        render_template("SELECT id FROM orders WHERE phone=:value", params)
