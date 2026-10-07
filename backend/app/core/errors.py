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


class SqlBlocked(AppError):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code, http_status=403)


class DatasourceUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.DATASOURCE_UNAVAILABLE, http_status=503)
