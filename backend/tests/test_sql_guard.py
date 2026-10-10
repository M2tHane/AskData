import pytest
import sqlglot
from sqlglot import exp

from app.core.errors import ErrorCode, SqlBlocked
from app.core.masking import MaskKind
from app.core.permission import RowFilter, RowOperator, TableScope, VisibleScope


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders",
        "SELECT o.id, c.name FROM orders o JOIN customers c ON o.customer_id=c.id",
        "SELECT o.id FROM orders o WHERE EXISTS "
        "(SELECT 1 FROM customers c WHERE c.id=o.customer_id)",
        "SELECT id FROM orders WHERE id IN (SELECT id FROM customers)",
        "SELECT (SELECT max(id) FROM customers) AS maximum FROM orders",
        "WITH x AS (SELECT id, amount FROM orders) SELECT id FROM x WHERE amount > 10",
        "WITH x AS (SELECT id FROM orders), y AS (SELECT id FROM x) SELECT id FROM y",
        "SELECT z.id FROM (SELECT id FROM orders) z",
        "SELECT a.id FROM orders a JOIN orders b ON a.id=b.id",
        "SELECT id FROM orders UNION ALL SELECT id FROM customers",
        "SELECT id FROM orders UNION SELECT id FROM customers",
        "SELECT COUNT(*) AS n FROM orders",
        "SELECT customer_id, SUM(amount) AS total FROM orders "
        "GROUP BY customer_id ORDER BY total DESC",
        "SELECT id, row_number() OVER "
        "(PARTITION BY customer_id ORDER BY amount DESC) AS n FROM orders",
        "SELECT CASE WHEN amount>10 THEN 1 ELSE 0 END AS tier FROM orders",
        "SELECT id AS amount FROM orders ORDER BY amount",
        "SELECT c.* FROM customers c",
        "SELECT * FROM (SELECT id FROM orders) a",
        "SELECT 1 AS one",
        "SELECT id FROM orders; -- trailing comment",
        "SELECT id FROM orders WHERE phone='drop table; pg_sleep(2)'",
    ],
)
def test_safe_queries(guard, scope, sql, dialect):
    result = guard.check(sql, dialect, scope)
    assert result.authentic()
    assert len(sqlglot.parse(result.normalized_sql, read=dialect)) == 1
    assert "secret" not in result.normalized_sql


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
@pytest.mark.parametrize(
    "sql,code",
    [
        ("INSERT INTO orders(id) VALUES (1)", ErrorCode.SQL_NOT_SELECT),
        ("UPDATE orders SET amount=0", ErrorCode.SQL_NOT_SELECT),
        ("DELETE FROM orders", ErrorCode.SQL_NOT_SELECT),
        ("DROP TABLE orders", ErrorCode.SQL_NOT_SELECT),
        ("ALTER TABLE orders ADD x INT", ErrorCode.SQL_NOT_SELECT),
        ("TRUNCATE orders", ErrorCode.SQL_NOT_SELECT),
        ("CREATE TABLE x(id INT)", ErrorCode.SQL_NOT_SELECT),
        ("GRANT SELECT ON orders TO x", ErrorCode.SQL_NOT_SELECT),
        ("CALL dangerous()", ErrorCode.SQL_NOT_SELECT),
        ("SET x=1", ErrorCode.SQL_NOT_SELECT),
        ("SELECT id FROM orders FOR UPDATE", ErrorCode.SQL_NOT_SELECT),
        ("SELECT id FROM orders; SELECT id FROM customers", ErrorCode.SQL_MULTI_STATEMENT),
        ("SELECT id FROM orders; DROP TABLE orders", ErrorCode.SQL_MULTI_STATEMENT),
        ("SELECT id FROM orders WHERE secret='x'", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT secret FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT * FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT o.* FROM orders o", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM salaries", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders ORDER BY secret", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders GROUP BY id HAVING max(secret)='x'", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders WHERE id IN (SELECT id FROM salaries)", ErrorCode.SQL_OUT_OF_SCOPE),
        (
            "SELECT id FROM orders WHERE EXISTS (SELECT 1 FROM orders x WHERE x.secret='x')",
            ErrorCode.SQL_OUT_OF_SCOPE,
        ),
        (
            "SELECT z.hidden FROM (SELECT secret AS hidden FROM orders) z",
            ErrorCode.SQL_OUT_OF_SCOPE,
        ),
        (
            "WITH x AS (SELECT secret AS id FROM orders) SELECT id FROM x",
            ErrorCode.SQL_OUT_OF_SCOPE,
        ),
        ("WITH x AS (SELECT * FROM orders) SELECT id FROM x", ErrorCode.SQL_OUT_OF_SCOPE),
        ("WITH x AS (SELECT secret FROM orders) SELECT 1", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders UNION SELECT secret FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders o JOIN customers c ON o.secret=c.name", ErrorCode.SQL_OUT_OF_SCOPE),
        (
            "SELECT id FROM orders o JOIN customers c ON o.customer_id=c.id",
            ErrorCode.SQL_OUT_OF_SCOPE,
        ),
        ("SELECT unknown FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT x.id FROM orders o", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT 1 FROM pg_class", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT 1 FROM information_schema.tables", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT 1 FROM pg_catalog.pg_class", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT 1 FROM mysql.user", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT 1 FROM performance_schema.threads", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT 1 FROM sys.users", ErrorCode.SQL_SYSTEM_TABLE),
        ("SELECT id FROM orders WHERE amount=?", ErrorCode.SQL_PARSE_FAILED),
        ("SELECT id FROM orders WHERE amount=@x", ErrorCode.SQL_PARSE_FAILED),
        (
            "WITH RECURSIVE x AS (SELECT id FROM orders UNION ALL SELECT id FROM x) "
            "SELECT id FROM x",
            ErrorCode.SQL_OUT_OF_SCOPE,
        ),
        ("SELECT id, id FROM orders", ErrorCode.SQL_OUT_OF_SCOPE),
        ("SELECT id FROM orders WHERE (", ErrorCode.SQL_PARSE_FAILED),
        ("", ErrorCode.SQL_PARSE_FAILED),
    ],
)
def test_blocked_queries(guard, scope, sql, code, dialect):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(sql, dialect, scope)
    assert caught.value.code == code
    assert all(word not in caught.value.message for word in ("orders", "secret", "salaries"))


@pytest.mark.parametrize(
    "function",
    [
        "pg_sleep",
        "pg_sleep_for",
        "pg_read_file",
        "pg_read_binary_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "sleep",
        "benchmark",
        "load_file",
        "nextval",
        "setval",
        "set_config",
        "pg_notify",
        "get_lock",
        "release_lock",
        "unregistered_udf",
    ],
)
@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_dangerous_functions(guard, scope, function, dialect):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(f"SELECT {function}(1)", dialect, scope)
    assert caught.value.code == ErrorCode.SQL_FORBIDDEN_FUNC


@pytest.mark.parametrize(
    "sql,dialect",
    [
        ("WITH x AS (DELETE FROM orders RETURNING id) SELECT * FROM x", "postgres"),
        ("COPY orders TO '/tmp/out'", "postgres"),
        ("SELECT id INTO stolen FROM orders", "postgres"),
        ("SELECT id FROM orders FOR SHARE", "postgres"),
        ("SELECT id FROM orders INTO OUTFILE '/tmp/out'", "mysql"),
        ("SELECT id FROM orders INTO DUMPFILE '/tmp/out'", "mysql"),
    ],
)
def test_dialect_write_forms(guard, scope, sql, dialect):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(sql, dialect, scope)
    assert caught.value.code in {ErrorCode.SQL_NOT_SELECT, ErrorCode.SQL_PARSE_FAILED}


def test_star_expansion_records_fields(guard, scope):
    result = guard.check("SELECT * FROM customers", "postgres", scope)
    assert "*" not in result.normalized_sql
    assert result.columns == {"customers": frozenset({"id", "name"})}


def test_schema_qualified_and_quoted(guard):
    scope = VisibleScope(
        datasource_id=1,
        tables={
            "public.orders": TableScope(
                all_columns={"id": "INT", "Case": "TEXT"},
                columns={"id", "Case"},
            )
        },
    )
    result = guard.check('SELECT o."Case" FROM public.orders o', "postgres", scope)
    assert result.columns["public.orders"] == {"Case"}
    with pytest.raises(SqlBlocked):
        guard.check("SELECT o.Case FROM public.orders o", "postgres", scope)


def test_cte_shadow_does_not_authorize_hidden_base(guard, scope):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(
            "WITH orders AS (SELECT id FROM salaries) SELECT * FROM orders", "postgres", scope
        )
    assert caught.value.code == ErrorCode.SQL_OUT_OF_SCOPE


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT phone AS p FROM orders",
        "WITH x AS (SELECT phone AS p FROM orders) SELECT p FROM x",
        "SELECT a.p FROM (SELECT phone AS p FROM orders) a",
        "SELECT phone AS p FROM orders UNION ALL SELECT phone FROM orders",
    ],
)
def test_masks_follow_lineage(guard, scope, sql):
    result = guard.check(sql, "postgres", scope)
    assert "p" in result.output_masks


def test_transformed_sensitive_output_is_fully_masked(guard, scope):
    result = guard.check("SELECT right(phone, 4) AS suffix FROM orders", "postgres", scope)
    assert result.output_masks["suffix"].kind == MaskKind.FULL


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_row_filters_wrap_every_reference_and_outer_join(guard, scope, dialect):
    scope.tables["orders"].row_filters = (
        RowFilter(column="tenant_id", value=7),
        RowFilter(column="id", operator=RowOperator.IN, value=[1, 2]),
    )
    result = guard.check(
        "SELECT c.id, o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id "
        "WHERE c.id=1 OR c.id=2",
        dialect,
        scope,
    )
    tree = sqlglot.parse_one(result.normalized_sql, read=dialect)
    inner = next(tree.find_all(exp.Subquery)).this
    assert inner.args["where"]
    assert "tenant_id" in inner.sql() and "7" in inner.sql()
    assert isinstance(tree.args["joins"][0].this, exp.Subquery)
    assert result.authentic()


def test_row_filter_value_is_not_executable(guard, scope):
    scope.tables["orders"].row_filters = (RowFilter(column="phone", value="'; DROP TABLE x;--"),)
    result = guard.check("SELECT id FROM orders", "postgres", scope)
    assert len(sqlglot.parse(result.normalized_sql, read="postgres")) == 1


def test_invalid_row_policy_fails_closed(guard, scope):
    scope.tables["orders"].row_filters = (RowFilter(column="unknown", value=1),)
    with pytest.raises(SqlBlocked):
        guard.check("SELECT id FROM orders", "postgres", scope)


def test_tampered_capability_loses_authenticity(guard, scope):
    result = guard.check("SELECT id FROM orders", "postgres", scope)
    assert not result.model_copy(update={"normalized_sql": "DELETE FROM orders"}).authentic()


# Tests for new dialects: kingbase, gaussdb, clickhouse
@pytest.mark.parametrize("dialect", ["kingbase", "gaussdb", "clickhouse"])
def test_new_dialects_accept_select(guard, scope, dialect):
    """New dialects should accept basic SELECT queries."""
    result = guard.check("SELECT id FROM orders", dialect, scope)
    assert result.authentic()


@pytest.mark.parametrize("dialect", ["kingbase", "gaussdb"])
def test_postgres_compatible_dialects(guard, scope, dialect):
    """Kingbase and GaussDB are PostgreSQL-compatible."""
    result = guard.check(
        "SELECT id FROM orders WHERE amount > 10 ORDER BY id LIMIT 5", dialect, scope
    )
    assert result.authentic()


def test_clickhouse_rejects_system_tables(guard, scope):
    """ClickHouse should reject queries against system tables."""
    with pytest.raises(SqlBlocked) as caught:
        guard.check("SELECT 1 FROM system.tables", "clickhouse", scope)
    assert caught.value.code == ErrorCode.SQL_SYSTEM_TABLE


def test_clickhouse_rejects_forbidden_functions(guard, scope):
    """ClickHouse should reject table functions that access external resources."""
    forbidden_functions = [
        "SELECT * FROM url('http://...')",
        "SELECT * FROM file('/path/to/file')",
        "SELECT * FROM s3('bucket/key')",
        "SELECT * FROM remote('host', 'db', 'table')",
    ]
    for sql in forbidden_functions:
        with pytest.raises(SqlBlocked) as caught:
            guard.check(sql, "clickhouse", scope)
        assert caught.value.code == ErrorCode.SQL_FORBIDDEN_FUNC


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT toStartOfMonth(o.created_at) AS m, sum(o.amount) AS s FROM orders o "
        "GROUP BY m ORDER BY m",
        "SELECT countIf(o.amount > 10) AS c, sumIf(o.amount, o.amount > 10) AS s FROM orders o",
        "SELECT toDate(o.created_at) AS d, uniq(o.customer_id) AS u, round(avg(o.amount), 2) AS a "
        "FROM orders o GROUP BY d",
        "SELECT multiIf(o.amount > 100, 'big', 'small') AS k, count() AS c "
        "FROM orders o GROUP BY k",
        "SELECT dateDiff('day', o.created_at, today()) AS d, toYear(o.created_at) AS y "
        "FROM orders o",
    ],
)
def test_clickhouse_common_functions_pass(guard, scope, sql):
    orders = scope.tables["orders"]
    timed = VisibleScope(
        datasource_id=1,
        tables={
            "orders": TableScope(
                all_columns={**orders.all_columns, "created_at": "DATETIME"},
                columns=orders.columns | {"created_at"},
                masks=orders.masks,
            )
        },
    )
    guard.check(sql, "clickhouse", timed)


@pytest.mark.parametrize(
    "sql",
    [
        # ClickHouse-only functions stay rejected for other engines.
        "SELECT uniq(customer_id) AS u FROM orders",
        "SELECT today() AS d FROM orders",
    ],
)
def test_clickhouse_functions_not_allowed_elsewhere(guard, scope, sql):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(sql, "postgres", scope)
    assert caught.value.code in {ErrorCode.SQL_FORBIDDEN_FUNC, ErrorCode.SQL_PARSE_FAILED}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders SETTINGS max_execution_time = 0",
        "SELECT id FROM orders FORMAT JSON",
    ],
)
def test_clickhouse_settings_and_format_clauses_are_rejected(guard, scope, sql):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(sql, "clickhouse", scope)
    assert caught.value.code == ErrorCode.SQL_FORBIDDEN_FUNC
