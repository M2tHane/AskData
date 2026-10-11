import re
from enum import StrEnum


class ErrorCode(StrEnum):
    OK = "OK"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    AUTH_FAILED = "AUTH_FAILED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DATASOURCE_UNAVAILABLE = "DATASOURCE_UNAVAILABLE"
    SQL_NOT_SELECT = "SQL_NOT_SELECT"
    SQL_MULTI_STATEMENT = "SQL_MULTI_STATEMENT"
    SQL_FORBIDDEN_FUNC = "SQL_FORBIDDEN_FUNC"
    SQL_SYSTEM_TABLE = "SQL_SYSTEM_TABLE"
    SQL_OUT_OF_SCOPE = "SQL_OUT_OF_SCOPE"
    SQL_PARSE_FAILED = "SQL_PARSE_FAILED"
    SQL_COST_EXCEEDED = "SQL_COST_EXCEEDED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_CANCELLED = "QUERY_CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    TASK_FAILED = "TASK_FAILED"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"


# Public messages deliberately contain no SQL or object names.
MESSAGES: dict[ErrorCode, str] = {
    code: code.value.replace("_", " ").capitalize() for code in ErrorCode
}
MESSAGES[ErrorCode.OK] = "Success"


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str | None = None, http_status: int = 400) -> None:
        self.code = code
        self.message = message or MESSAGES[code]
        self.http_status = http_status
        super().__init__(self.message)


class AuthenticationError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.AUTH_FAILED, http_status=401)


class PermissionDenied(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.FORBIDDEN, http_status=403)


class BlockRule(StrEnum):
    """Which check refused the SQL. A fixed vocabulary, safe to log and to show to admins."""

    PARSE_ERROR = "parse_error"
    MULTI_STATEMENT = "multi_statement"
    NOT_SELECT = "not_select"
    WRITE_STATEMENT = "write_statement"
    QUERY_SETTINGS = "query_settings"
    PARAMETER = "parameter"
    RECURSIVE_CTE = "recursive_cte"
    USER_DEFINED_TYPE = "user_defined_type"
    DANGEROUS_FUNCTION = "dangerous_function"
    FUNCTION_NOT_ALLOWED = "function_not_allowed"
    QUALIFIED_FUNCTION = "qualified_function"
    SYSTEM_TABLE = "system_table"
    CROSS_CATALOG = "cross_catalog"
    TABLE_NOT_VISIBLE = "table_not_visible"
    COLUMN_WITHHELD = "column_withheld"
    UNRESOLVED_COLUMN = "unresolved_column"
    STAR_PROJECTION = "star_projection"
    DUPLICATE_OUTPUT = "duplicate_output"
    LINEAGE_UNKNOWN = "lineage_unknown"
    ROW_FILTER_INVALID = "row_filter_invalid"
    NOT_GUARDED = "not_guarded"
    DATASOURCE_MISMATCH = "datasource_mismatch"
    NOT_READ_ONLY = "not_read_only"
    COST_EXCEEDED = "cost_exceeded"
    WRITE_REQUEST = "write_request"
    UNANSWERABLE = "unanswerable"
    CORRECTION_OUT_OF_SCOPE = "correction_out_of_scope"


# A subject is kept only when it is a plain identifier (a function name), never free text.
_SUBJECT = re.compile(r"[a-z_][a-z0-9_]{0,63}")


class SqlBlocked(AppError):
    """A refusal. rule says which check fired; subject names a public SQL function at most.

    Neither ever carries the SQL, a hidden table or column, or data.
    """

    def __init__(
        self, code: ErrorCode, rule: BlockRule | None = None, subject: str | None = None
    ) -> None:
        super().__init__(code, http_status=403)
        self.rule = rule
        name = (subject or "").lower()
        self.subject = name if _SUBJECT.fullmatch(name) else None


class UnresolvedReference(SqlBlocked):
    """A field the SQL names cannot be resolved against the visible schema.

    Only for names that exist nowhere in the table: a withheld column is a plain SqlBlocked
    and never gets a correction round. The detail goes back to the model so it can correct
    its own SQL; it is never shown to users or logged.
    """

    def __init__(self, reference: str) -> None:
        super().__init__(ErrorCode.SQL_OUT_OF_SCOPE, BlockRule.UNRESOLVED_COLUMN)
        self.detail = f"无法解析字段 {reference}" if reference else "SQL 中有无法解析的字段引用"


def database_state(state: object) -> str | None:
    """A database error code as a diagnostic subject: sqlstate_42803 or mysql_1054.

    Only the standard code is kept, never the driver's message, which can quote data,
    hidden names or credentials.
    """
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        return f"sqlstate_{state.lower()}"
    if isinstance(state, int) and 0 < state < 100000:
        return f"mysql_{state}"
    return None


class DatasourceUnavailable(AppError):
    def __init__(self, state: object = None) -> None:
        super().__init__(ErrorCode.DATASOURCE_UNAVAILABLE, http_status=503)
        self.subject = database_state(state)


# Fixed correction hints per SQLSTATE. The model sees these, never the driver's message.
EXECUTION_HINTS = {
    "42803": (
        "分组错误：SELECT/ORDER BY 中的非聚合列必须出现在 GROUP BY 中；"
        "窗口函数里引用分组后的值要写成 SUM(SUM(x)) OVER ()。"
    ),
    "42702": "字段名有歧义：多表关联时每个字段都要加表别名。",
    "42883": "函数或运算符的参数类型不匹配：先用 CAST 转成合适的类型。",
    "42804": "类型不匹配：比较、CASE 或 UNION 两边的类型不同，先用 CAST 统一。",
    "22012": "除以零：分母写成 NULLIF(x, 0)。",
    "22P02": "数值或日期的字面量格式无效：检查字面量和 CAST。",
}


class SqlExecutionFailed(AppError):
    """The database rejected the SQL itself (syntax, grouping, types); a correction may fix it."""

    def __init__(self, state: object = None) -> None:
        super().__init__(ErrorCode.SQL_EXECUTION_ERROR)
        self.subject = database_state(state)
        self.detail = EXECUTION_HINTS.get(state, "") if isinstance(state, str) else ""
