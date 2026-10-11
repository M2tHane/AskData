import json
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import ErrorCode
from app.core.response import Envelope

request_id: ContextVar[str] = ContextVar("request_id", default="")


class LogEvent(StrEnum):
    REQUEST = "request"
    QUERY = "query"
    MODEL = "model"
    TASK = "task"
    AUDIT = "audit"
    FAILURE = "failure"
    # A question that failed or was refused: stage, rule, error type, evaluation case.
    DIAGNOSTIC = "diagnostic"
    RETENTION = "retention"


_ALLOWED_FIELDS = {
    "code",
    "status",
    "duration_ms",
    "row_count",
    "tokens_in",
    "tokens_out",
    "model_id",
    "prompt_version",
    "user_id",
    "team_id",
    "datasource_id",
    "task_id",
    "action",
    "count",
    "retries",
    "query_run_id",
    "eval_run_id",
}
_SENSITIVE = re.compile(
    r"password|secret|token|authorization|api.?key|result|preview|content", re.I
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if hasattr(value, "get_secret_value"):
        return "[REDACTED]"
    return value


class SafeLogger:
    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._sink = sink

    def info(self, event: LogEvent, **fields: Any) -> None:
        if not isinstance(event, LogEvent):
            raise ValueError("Log events must be registered")
        payload: dict[str, Any] = {
            key: value
            for key, value in fields.items()
            if key in _ALLOWED_FIELDS and isinstance(value, (int, float, bool))
        }
        # String fields have constrained formats; arbitrary SQL/results/errors are never logged.
        for key in (
            "code",
            "status",
            "task_id",
            "model_id",
            "prompt_version",
            "action",
            "stage",
            "rule",
            "subject",
            "error_type",
            "eval_case_id",
            "retry_rules",
        ):
            value = fields.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", value):
                payload[key] = value
        payload.update(event=event.value, request_id=request_id.get())
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
        if self._sink:
            self._sink(payload)
        else:
            structlog.get_logger("askdata").info(
                event.value, **{key: value for key, value in payload.items() if key != "event"}
            )


logger = SafeLogger()


def configure_logging() -> None:
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        trace.set_tracer_provider(
            TracerProvider(resource=Resource.create({"service.name": "askdata"}))
        )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(serializer=json.dumps),
        ]
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            identifier = str(UUID(headers.get(b"x-request-id", b"").decode()))
        except (ValueError, UnicodeError):
            identifier = str(uuid4())
        token = request_id.set(identifier)
        started, status = False, 500
        start = time.monotonic()

        async def send_with_id(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started, status = True, message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", identifier.encode())
                ]
            await send(message)

        try:
            with trace.get_tracer("askdata").start_as_current_span(
                "http.request",
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                span.set_attribute("request.id", identifier)
                try:
                    await self.app(scope, receive, send_with_id)
                except Exception:
                    logger.info(LogEvent.FAILURE, code=ErrorCode.INTERNAL_ERROR)
                    if started:
                        raise
                    response = JSONResponse(
                        status_code=500,
                        content=Envelope[Any](
                            code=ErrorCode.INTERNAL_ERROR,
                            message="Internal error",
                        ).model_dump(mode="json"),
                    )
                    await response(scope, receive, send_with_id)
                finally:
                    logger.info(
                        LogEvent.REQUEST,
                        status=status,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
        finally:
            request_id.reset(token)
