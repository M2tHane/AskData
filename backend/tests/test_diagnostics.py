"""Safe diagnostics: which rule refused a query, never the SQL, data or a hidden name."""

import pytest

from app.core.deps import CurrentUser, Role
from app.core.errors import BlockRule, ErrorCode, SqlBlocked
from app.core.logging import LogEvent, SafeLogger
from app.modules.chat.pipeline import QueryContext, diagnose


@pytest.mark.parametrize(
    ("sql", "rule", "subject"),
    [
        # Known functions are named as sqlglot knows them.
        (
            "SELECT generate_series(1, 10) AS n",
            BlockRule.FUNCTION_NOT_ALLOWED,
            "exploding_generate_series",
        ),
        # An unknown function name is model text: it could spell a hidden table, so it is dropped.
        ("SELECT employee_salary(id) AS s FROM orders", BlockRule.FUNCTION_NOT_ALLOWED, None),
        ("SELECT pg_read_file('/etc/passwd') AS f", BlockRule.DANGEROUS_FUNCTION, "pg_read_file"),
        ("WITH RECURSIVE t AS (SELECT 1 AS n) SELECT n FROM t", BlockRule.RECURSIVE_CTE, None),
        ("SELECT secret FROM orders", BlockRule.COLUMN_WITHHELD, None),
        ("SELECT id FROM salaries", BlockRule.TABLE_NOT_VISIBLE, None),
        ("SELECT id FROM orders; DELETE FROM orders", BlockRule.MULTI_STATEMENT, None),
        ("DELETE FROM orders", BlockRule.NOT_SELECT, None),
        ("SELECT relname FROM pg_catalog.pg_class", BlockRule.SYSTEM_TABLE, None),
        ("SELECT nonexistent FROM orders", BlockRule.UNRESOLVED_COLUMN, None),
    ],
)
def test_each_refusal_names_its_rule_and_only_a_known_function(guard, scope, sql, rule, subject):
    with pytest.raises(SqlBlocked) as caught:
        guard.check(sql, "postgres", scope)
    assert caught.value.rule == rule
    assert caught.value.subject == subject


def test_parse_failures_record_the_error_type_not_the_message(guard, scope):
    with pytest.raises(SqlBlocked) as caught:
        guard.check("SELECT FROM WHERE (((", "postgres", scope)
    assert caught.value.rule == BlockRule.PARSE_ERROR
    assert caught.value.subject and caught.value.subject.endswith("error")


def test_subject_must_be_a_plain_identifier():
    assert (
        SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC, BlockRule.FUNCTION_NOT_ALLOWED, "x; drop").subject
        is None
    )
    assert SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC, subject="订单").subject is None


def test_diagnosis_and_log_line_carry_only_safe_fields():
    member = CurrentUser(id=7, role=Role.MEMBER, team_id=1)
    ctx = QueryContext(user=member, datasource_id=3, conversation_id=0, question="q")
    ctx.stage, ctx.status, ctx.query_run_id = "guard", "blocked", 41
    ctx.eval_ref = {"run_id": 12, "case_id": "C06"}
    error = SqlBlocked(
        ErrorCode.SQL_FORBIDDEN_FUNC, BlockRule.FUNCTION_NOT_ALLOWED, "generate_series"
    )
    ctx.diagnostic = diagnose(ctx, error)
    assert ctx.diagnostic == {
        "stage": "guard",
        "error_type": "SqlBlocked",
        "code": "SQL_FORBIDDEN_FUNC",
        "rule": "function_not_allowed",
        "subject": "generate_series",
        "eval_run_id": 12,
        "eval_case_id": "C06",
    }
    lines: list[dict] = []
    logger = SafeLogger(lines.append)
    from app.modules.chat import pipeline

    original, pipeline.logger = pipeline.logger, logger
    try:
        pipeline.log_diagnostic(ctx)
    finally:
        pipeline.logger = original
    [line] = lines
    assert line["event"] == LogEvent.DIAGNOSTIC.value
    assert {k: line[k] for k in ("stage", "rule", "subject", "eval_case_id", "eval_run_id")} == {
        "stage": "guard",
        "rule": "function_not_allowed",
        "subject": "generate_series",
        "eval_case_id": "C06",
        "eval_run_id": 12,
    }
    assert line["query_run_id"] == 41 and line["code"] == "SQL_FORBIDDEN_FUNC"
    assert "question" not in line and "sql" not in line


def test_refusal_after_correction_keeps_the_rule_each_attempt_hit():
    member = CurrentUser(id=7, role=Role.MEMBER, team_id=1)
    ctx = QueryContext(user=member, datasource_id=3, conversation_id=0, question="q")
    ctx.stage, ctx.status = "correction", "blocked"
    ctx.retries = [
        {"attempt": 1, "code": "SQL_SYSTEM_TABLE", "rule": "system_table"},
        {"attempt": 2, "code": "SQL_EXECUTION_ERROR"},
    ]
    ctx.diagnostic = diagnose(ctx, SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.UNANSWERABLE))
    assert ctx.diagnostic["retries"] == 2
    assert ctx.diagnostic["retry_rules"] == ["system_table", "SQL_EXECUTION_ERROR"]
    lines: list[dict] = []
    from app.modules.chat import pipeline

    original, pipeline.logger = pipeline.logger, SafeLogger(lines.append)
    try:
        pipeline.log_diagnostic(ctx)
    finally:
        pipeline.logger = original
    assert lines[0]["retry_rules"] == "system_table/SQL_EXECUTION_ERROR"
