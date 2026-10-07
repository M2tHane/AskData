import pytest

from app.core.errors import ErrorCode, SqlBlocked
from app.core.masking import MaskKind
from app.core.permission import RowFilter


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST('payload' AS unknown_custom_type)",
        "SELECT pg_catalog.lower('x')",
        "SELECT id FROM orders WHERE EXISTS (SELECT 1 FROM customers WHERE orders.secret=name)",
        "SELECT phone AS secret FROM orders WHERE secret='x'",
        "WITH x AS (SELECT id FROM orders), y AS (SELECT secret FROM orders) SELECT id FROM x",
        "SELECT COUNT(secret) FROM orders",
        "SELECT row_number() OVER (ORDER BY secret) AS rank FROM orders",
        "SELECT 1 FROM foreign_schema.orders",
        "SELECT (SELECT secret FROM orders LIMIT 1) AS x",
    ],
)
def test_adversarial_edges(guard, scope, sql):
    with pytest.raises(SqlBlocked):
        guard.check(sql, "postgres", scope)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT CONCAT(phone, phone) AS p FROM orders) SELECT p FROM x",
        "SELECT x.p FROM (SELECT upper(phone) AS p FROM orders) x",
        "SELECT max(phone) AS p FROM orders",
    ],
)
def test_transformed_masks_through_derived_aliases_are_full(guard, scope, sql):
    assert guard.check(sql, "postgres", scope).output_masks["p"].kind == MaskKind.FULL


def test_row_policy_in_self_join_and_cte(guard, scope):
    scope.tables["orders"].row_filters = (RowFilter(column="tenant_id", value=7),)
    result = guard.check(
        "WITH x AS (SELECT id FROM orders) SELECT a.id FROM orders a "
        "JOIN orders b ON a.id=b.id WHERE a.id IN (SELECT id FROM x)",
        "postgres",
        scope,
    )
    assert result.normalized_sql.count('"tenant_id" = 7') == 3


def test_raw_or_malformed_guard_inputs_are_registered_errors(guard, scope):
    with pytest.raises(SqlBlocked) as caught:
        guard.check("SELECT 1", "not-a-dialect", scope)
    assert caught.value.code == ErrorCode.SQL_PARSE_FAILED
    with pytest.raises(SqlBlocked):
        guard.check(None, "postgres", scope)
    assert (
        not guard.check("SELECT 1", "postgres", scope)
        .model_copy(update={"seal": "中文"})
        .authentic()
    )


def test_mysql_executable_comment_never_survives_normalization(guard, scope):
    result = guard.check(
        "SELECT id FROM orders /*! UNION SELECT secret FROM orders */", "mysql", scope
    )
    assert "secret" not in result.normalized_sql and "/*!" not in result.normalized_sql
