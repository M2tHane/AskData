import json
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import AppError, ErrorCode
from app.core.scope import ScopeMixin


class ConversationCreate(BaseModel):
    datasource_id: int = Field(ge=1)
    title: str = Field(default="新会话", min_length=1, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        return value


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    team_id: int | None
    datasource_id: int
    title: str
    created_at: datetime
    # Last activity: creation, a new message or a rename. The list is ordered by it.
    updated_at: datetime


class ChartSpec(BaseModel):
    """Restricted ECharts input; never accepts JavaScript or arbitrary options."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "pie", "scatter", "table"]
    x: str | None = None
    y: list[str] = Field(default_factory=list, max_length=20)
    series: str | None = None
    stack: bool = False
    title: str = Field(default="", max_length=200)
    # Display names for result columns (column -> short Chinese label); unknown keys are dropped.
    labels: dict[str, str] = Field(default_factory=dict, max_length=50)
    # Value format per result column; the front end formats, the data stays numeric.
    formats: dict[str, Literal["number", "integer", "percent", "currency"]] = Field(
        default_factory=dict, max_length=50
    )
    # Measures drawn on a secondary (right) axis when their scale differs from the others.
    y_right: list[str] = Field(default_factory=list, max_length=5)


class UsedDefinition(BaseModel):
    """A semantic term or metric the answer actually relied on, shown to the user."""

    kind: Literal["semantic", "metric", "enum"]
    name: str
    scope: str
    version: int = 1
    description: str = ""


class AnswerMeta(BaseModel):
    """Structured explanation of one answer; the answer text itself is the message content."""

    interpretation: str = Field(default="", max_length=500)
    filters: list[str] = Field(default_factory=list, max_length=20)
    definitions: list[UsedDefinition] = Field(default_factory=list, max_length=20)
    # Only the relative time expressions found in the question, resolved by the backend.
    time_ranges: dict[str, dict[str, str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list, max_length=10)
    reasoning: str = Field(default="", max_length=20000)
    # The clarification choice the user picked for this answer, if any.
    clarification_choice: str | None = None
    # Parallel to a pending clarification's choices: the verified table.column or metric refs.
    clarification_refs: list[str] = Field(default_factory=list, max_length=8)
    # The business question this answer is about. For an edited-SQL rerun it is the question
    # of the answer that was edited, so reruns of reruns still point at the real question.
    question: str = Field(default="", max_length=2000)


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: int
    conversation_id: int
    role: Literal["user", "assistant"]
    content: str
    normalized_sql: str | None = None
    query_run_id: int | None = None
    created_at: datetime
    clarification_question: str | None = None
    clarification_choices: list[str] = Field(default_factory=list)
    clarification_answered: bool = False
    chart: ChartSpec | None = Field(default=None, validation_alias="chart_spec")
    meta: AnswerMeta | None = Field(default=None, validation_alias="answer_meta")

    @field_validator("chart", mode="before")
    @classmethod
    def decode_chart(cls, value: Any) -> Any:
        return ChartSpec.model_validate_json(value) if isinstance(value, str) else value

    @field_validator("meta", mode="before")
    @classmethod
    def decode_meta(cls, value: Any) -> Any:
        return AnswerMeta.model_validate_json(value) if isinstance(value, str) else value

    @field_validator("clarification_choices", mode="before")
    @classmethod
    def decode_choices(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value or []


class TimezoneRequest(BaseModel):
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise AppError(ErrorCode.VALIDATION_ERROR) from None
        return value


class QuestionRequest(TimezoneRequest):
    question: str = Field(min_length=1, max_length=10000)
    clarification_message_id: int | None = Field(default=None, ge=1)
    clarification_choice: str | None = Field(default=None, max_length=300)
    # Metrics the user @mentioned; only ones visible to the caller are honored.
    referenced_metric_ids: list[int] = Field(default_factory=list, max_length=5)
    # Optional model chosen in the composer; must be an enabled LLM configuration.
    model_id: int | None = Field(default=None, ge=1)


class RerunRequest(TimezoneRequest):
    sql: str = Field(min_length=1, max_length=50000)
    # The answer whose SQL was edited; its chart is reused when the columns still fit.
    source_message_id: int | None = Field(default=None, ge=1)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    choices: list[str] = Field(min_length=2, max_length=8)


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=3000)
    filters: list[str] = Field(default_factory=list, max_length=30)


class ContextItem(ScopeMixin):
    kind: Literal["semantic", "metric", "enum", "example"]
    ref_id: int | None = None
    key: str
    name: str
    version: int = 1
    definition: str
    # Human-readable description shown when the answer cites this item.
    description: str = ""
    tables: dict[str, frozenset[str]]
    score: float = 1
    # Longest name or alias found literally in the question ("" when not matched literally).
    matched: str = ""


class RetrievalBundle(BaseModel):
    tables: dict[str, dict[str, str]] = Field(default_factory=dict)
    items: list[ContextItem] = Field(default_factory=list)
    required_conditions: list[str] = Field(default_factory=list)
    # Providers must return structured, pre-authorized choices (not generated hidden names).
    ambiguities: list[Clarification] = Field(default_factory=list)
    mode: Literal["full", "hybrid"] = "full"
    # Keys of items the user pinned with an @mention; the model must reuse them as defined.
    pinned: list[str] = Field(default_factory=list)
    # Terms defined at several levels: name -> the level (platform/team/personal) in effect.
    definition_levels: dict[str, str] = Field(default_factory=dict)


class QueryReply(BaseModel):
    status: Literal["success", "blocked", "failed", "clarify"]
    query_run_id: int | None = None
    message_id: int | None = None
    code: ErrorCode = ErrorCode.OK
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    row_count: int = 0
    preview_truncated: bool = False
    chart: ChartSpec | None = None
    # The data-grounded answer text (message content).
    explanation: str = ""
    meta: AnswerMeta | None = None
    clarification: Clarification | None = None
    title: str | None = None


class ExportFile(BaseModel):
    filename: str
    content_type: str
    content: bytes = Field(repr=False)
    row_count: int = 0
    # The export stopped at the row or size cap; the file holds the first rows only.
    truncated: bool = False


class ChatLimits(BaseModel):
    """Caps the UI states next to results and exports."""

    preview_rows: int
    export_max_rows: int
    export_max_mb: int


class ContextItemView(BaseModel):
    kind: Literal["semantic", "metric", "enum", "example"]
    name: str
    scope: str
    version: int
    definition: str
    pinned: bool = False


class ContextPreviewRequest(BaseModel):
    datasource_id: int = Field(ge=1)
    question: str = Field(default="", max_length=2000)
    referenced_metric_ids: list[int] = Field(default_factory=list, max_length=5)


class ContextPreview(BaseModel):
    """What the model would receive for this user: already limited to their visible scope."""

    mode: Literal["full", "hybrid"]
    tables: dict[str, dict[str, str]]
    items: list[ContextItemView]
    required_conditions: list[str]
    ambiguities: list[Clarification]


class SuggestedQuestion(BaseModel):
    """One starter question and the visible metric or table it is about, when known."""

    question: str
    kind: Literal["metric", "table"] | None = None
    name: str | None = None


class SuggestedQuestions(BaseModel):
    questions: list[str]
    items: list[SuggestedQuestion] = []
    source: Literal["model", "rules"]
