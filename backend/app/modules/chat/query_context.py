"""The state one question carries through the pipeline stages."""

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from app.core.datasource import Datasource
from app.core.deps import CurrentUser
from app.core.errors import ErrorCode
from app.core.permission import VisibleScope
from app.core.sql.executor import QueryResult
from app.core.sql.guard import GuardedSql
from app.core.stream import EventType
from app.modules.chat.schemas import (
    AnswerMeta,
    ChartSpec,
    Clarification,
    MessageView,
    QueryReply,
    RetrievalBundle,
)

# Rows streamed to the browser and returned inline; exports re-run the guarded SQL in full.
PREVIEW_ROWS = 500
# Reasoning kept with an answer; the live stream is not cut.
MAX_REASONING_CHARS = 20_000


def plain_value(value: Any) -> Any:
    """JSON-number form of database decimals, identical for streamed and replayed answers."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        if value == value.to_integral_value() and abs(value) < 2**53:
            return int(value)
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def plain_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: plain_value(value) for key, value in row.items()} for row in rows]


@dataclass
class QueryContext:
    user: CurrentUser
    datasource_id: int
    conversation_id: int
    question: str
    timezone: str = "Asia/Shanghai"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    manual_sql: str | None = field(default=None, repr=False)
    clarification_used: bool = False
    clarification_choice: str | None = None
    # The verified table.column or metric ref behind the chosen option, when known.
    clarification_ref: str | None = None
    pinned_metric_ids: frozenset[int] = frozenset()
    requested_model_id: int | None = None
    limit_overrides: dict[str, Any] = field(default_factory=dict)
    message_id: int | None = None
    query_run_id: int | None = None
    query_id: str = field(default_factory=lambda: f"query:{uuid4()}")
    datasource: Datasource | None = field(default=None, repr=False)
    scope: VisibleScope | None = field(default=None, repr=False)
    history: list[MessageView] = field(default_factory=list, repr=False)
    retrieval: RetrievalBundle = field(default_factory=RetrievalBundle, repr=False)
    # Why retrieved context was replaced by the full schema ("unanswerable", "correction").
    retrieval_fallback: str = ""
    time_ranges: dict[str, Any] = field(default_factory=dict)
    follow_up: bool = False
    inherited_sql: str | None = None
    model_id: str = ""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    generated_sql: str | None = field(default=None, repr=False)
    guarded_sql: GuardedSql | None = field(default=None, repr=False)
    result: QueryResult | None = field(default=None, repr=False)
    retries: list[dict[str, Any]] = field(default_factory=list)
    chart: ChartSpec | None = None
    # Presentation proposed by the SQL call; validated against the real result columns.
    chart_hint: dict[str, Any] | None = None
    # Edited-SQL rerun: the answer it started from ({message_id, query_run_id, question}).
    rerun_of: dict[str, Any] | None = None
    # The data-grounded answer text (or the error message on failure).
    explanation: str = ""
    meta: AnswerMeta | None = None
    title: str = ""
    interpretation: str = ""
    filters: list[str] = field(default_factory=list)
    reasoning: str = field(default="", repr=False)
    clarification: Clarification | None = None
    clarification_refs: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "success"
    error_code: ErrorCode | None = None
    execution_error: ErrorCode | None = None
    # What could not be resolved in the last rejected SQL; only ever sent back to the model.
    error_detail: str = field(default="", repr=False)
    # Whether the one rewrite allowed for an unreviewed standard function has been used.
    function_rewritten: bool = False
    # Standard database error code of the last execution failure (sqlstate_42803).
    execution_subject: str | None = None
    # SQL the guard rejected. Never persisted or returned; evaluation shows it to admins.
    rejected_sql: str | None = field(default=None, repr=False)
    # The guard rule behind execution_error, for the correction log.
    execution_rule: str | None = None
    # The stage that was running when the question failed or was refused.
    stage: str = ""
    # Safe facts for finding a failure later (see diagnose): never SQL, data or hidden names.
    diagnostic: dict[str, Any] | None = None
    # Set by evaluation runs: {"run_id": int, "case_id": str}.
    eval_ref: dict[str, Any] | None = None
    # Set for MCP calls: {"source": "mcp"|"mcp_test", "client_id", "request_id", "rpc_id"}.
    origin: dict[str, Any] | None = None
    # The usual reading the model chose for an open dimension or measure, told to the user.
    assumption: str = ""
    warnings: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    emit: Callable[[EventType, dict[str, Any]], Awaitable[None]] | None = field(
        default=None, repr=False
    )

    async def event(self, kind: EventType, data: dict[str, Any]) -> None:
        if self.emit:
            await self.emit(kind, data)

    def snapshot(self) -> dict[str, Any]:
        # No secrets, withheld metadata, full results or raw provider exception text.
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "query_id": self.query_id,
            "question": self.question,
            "user_id": self.user.id,
            "team_id": self.user.team_id,
            **({"origin": self.origin} if self.origin else {}),
            "datasource_id": self.datasource_id,
            "model_id": self.model_id,
            "visible_schema": self.retrieval.tables,
            "retrieval": self.retrieval.model_dump(mode="json"),
            "history": [message.model_dump(mode="json") for message in self.history],
            "follow_up": self.follow_up,
            "inherited_sql": self.inherited_sql,
            "generated_sql": self.generated_sql,
            "time_ranges": self.time_ranges,
            "normalized_sql": self.guarded_sql.normalized_sql if self.guarded_sql else None,
            "guard": {
                "tables": sorted(self.guarded_sql.tables),
                "columns": {name: sorted(cols) for name, cols in self.guarded_sql.columns.items()},
            }
            if self.guarded_sql
            else None,
            "result_summary": {
                "row_count": self.result.row_count,
                "truncated": self.result.truncated,
            }
            if self.result
            else None,
            "retries": self.retries,
            # How much context the model got (counts only), and why it fell back to all of it.
            "retrieval_stats": {
                "mode": self.retrieval.mode,
                "tables": len(self.retrieval.tables),
                "columns": sum(len(columns) for columns in self.retrieval.tables.values()),
                "items": len(self.retrieval.items),
                **({"fallback": self.retrieval_fallback} if self.retrieval_fallback else {}),
            },
            "chart": self.chart.model_dump() if self.chart else None,
            "title": self.title,
            "interpretation": self.interpretation,
            "filters": self.filters,
            "clarification_choice": self.clarification_choice,
            "stage_timings": self.timings,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "prompt_versions": self.prompt_versions,
            "semantic": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind in {"semantic", "enum"}
            ],
            "metrics": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind == "metric"
            ],
            "examples": [
                item.model_dump(mode="json")
                for item in self.retrieval.items
                if item.kind == "example"
            ],
            "time_range": self.time_ranges,
            "retry_count": len(self.retries),
            "diagnostic": self.diagnostic,
            "eval": self.eval_ref,
            "status": self.status,
            "error": self.error_code,
            "warnings": self.warnings,
            "clarification": self.clarification.model_dump() if self.clarification else None,
            "rerun_of": self.rerun_of,
        }

    @property
    def business_question(self) -> str:
        """What the user actually asked; a rerun's own question is only a fixed marker."""
        return str((self.rerun_of or {}).get("question") or self.question)

    def reply(self) -> QueryReply:
        return QueryReply(
            status=self.status,  # type: ignore[arg-type]
            query_run_id=self.query_run_id,
            message_id=self.message_id,
            code=self.error_code or ErrorCode.OK,
            sql=self.guarded_sql.normalized_sql
            if self.guarded_sql and self.status == "success"
            else None,
            columns=self.result.columns if self.result and self.status == "success" else [],
            rows=plain_rows(self.result.rows[:PREVIEW_ROWS])
            if self.result and self.status == "success"
            else [],
            truncated=self.result.truncated if self.result else False,
            row_count=self.result.row_count if self.result else 0,
            preview_truncated=bool(self.result and len(self.result.rows) > PREVIEW_ROWS),
            chart=self.chart,
            explanation=self.explanation,
            meta=self.meta.model_copy(update={"question": self.business_question[:2000]})
            if self.meta
            else None,
            clarification=self.clarification,
        )

    def answer_meta(self) -> AnswerMeta:
        """What is persisted with the assistant message, for success and clarification alike."""
        meta = self.meta or AnswerMeta(
            interpretation=self.interpretation[:500], filters=self.filters[:20]
        )
        return meta.model_copy(
            update={
                "reasoning": self.reasoning[:MAX_REASONING_CHARS],
                "clarification_choice": self.clarification_choice,
                "clarification_refs": self.clarification_refs if self.clarification else [],
                "question": self.business_question[:2000],
            }
        )


class Stage(Protocol):
    name: str

    async def run(self, ctx: QueryContext) -> QueryContext: ...
