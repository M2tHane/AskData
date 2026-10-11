from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import AppError, ErrorCode

# Rows an agent may receive from one call, whatever the connection sets.
ROW_CEILING = 1000
# Characters of row data per call: wide rows run out of this before they run out of rows.
CHAR_BUDGET = 30_000


class McpClientWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None
    daily_call_limit: int = Field(default=2000, ge=1, le=1_000_000)
    default_rows: int = Field(default=100, ge=1, le=ROW_CEILING)
    max_rows: int = Field(default=ROW_CEILING, ge=1, le=ROW_CEILING)
    ask_timeout_seconds: int = Field(default=60, ge=10, le=120)
    allow_run_sql: bool = False

    @model_validator(mode="after")
    def rows_in_order(self) -> "McpClientWrite":
        if self.default_rows > self.max_rows:
            raise AppError(ErrorCode.VALIDATION_ERROR, "default_rows exceeds max_rows")
        return self


class McpKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class McpKeyCreated(BaseModel):
    """The full key appears here once; only its hash is kept."""

    key: str
    detail: McpKeyRead
    client_id: int | None = None


class McpKeyRotate(BaseModel):
    # How long the previous keys keep working; 0 revokes them at once.
    grace_hours: int = Field(default=0, ge=0, le=24)


class McpClientRead(McpClientWrite):
    id: int
    public_id: str
    user_id: int
    status: Literal["active", "disabled"]
    created_at: datetime
    last_used_at: datetime | None
    calls_today: int
    keys: list[McpKeyRead] = Field(default_factory=list)
    datasource_ids: list[int] = Field(default_factory=list)
    # Granted columns that a schema sync has since removed.
    stale_grants: int = 0
    override_count: int = 0


class McpClientStatus(BaseModel):
    active: bool


class SemanticEdit(BaseModel):
    aliases: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=10000)
    enum_mapping: dict[str, str | int | float | bool | None] | None = None


class MetricEdit(BaseModel):
    aliases: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=10000)
    sql_template: str | None = Field(default=None, min_length=1, max_length=50000)
    params: dict[str, str | int | float | bool | None] | None = None


class OverrideWrite(BaseModel):
    kind: Literal["semantic", "metric"]
    item_id: int = Field(ge=1)
    action: Literal["edit", "hide"]
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def payload_fits(self) -> "OverrideWrite":
        if self.action == "hide":
            self.payload = {}
            return self
        model = SemanticEdit if self.kind == "semantic" else MetricEdit
        edit = model.model_validate(self.payload)
        self.payload = edit.model_dump(exclude_none=True)
        if not self.payload:
            raise AppError(ErrorCode.VALIDATION_ERROR, "an edit changes at least one field")
        return self


class OverrideRestore(BaseModel):
    """Restore platform defaults: one item, one kind, or (both empty) everything."""

    kind: Literal["semantic", "metric"] | None = None
    item_id: int | None = Field(default=None, ge=1)


class OverlayItem(BaseModel):
    """A platform item as one connection sees it."""

    kind: Literal["semantic", "metric"]
    item_id: int
    name: str
    platform: dict[str, Any]
    effective: dict[str, Any]
    state: Literal["default", "edited", "hidden"]


class AgentDataset(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    # Rows the query produced (up to the database row limit).
    row_count: int
    returned_rows: int
    # More rows exist than were returned, by the row cap or the character budget.
    truncated: bool
    truncated_by: Literal["rows", "chars", "database"] | None = None


class AgentUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


class AgentMetric(BaseModel):
    name: str
    source: Literal["platform", "connection"]


class AskResult(BaseModel):
    status: Literal["success", "empty", "clarify"]
    request_id: str
    conversation_id: str
    answer: str = ""
    sql: str | None = None
    data: AgentDataset | None = None
    assumption: str = ""
    # When status is clarify: pick one and call ask again with the same conversation_id.
    question: str | None = None
    options: list[str] = Field(default_factory=list)
    metrics_used: list[AgentMetric] = Field(default_factory=list)
    usage: AgentUsage = Field(default_factory=AgentUsage)


class SqlResult(BaseModel):
    status: Literal["success", "empty"]
    request_id: str
    sql: str
    data: AgentDataset
    usage: AgentUsage = Field(default_factory=AgentUsage)


class AgentError(BaseModel):
    """A tool failure: is_error is set on the MCP result and this is its structured content."""

    status: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False
    request_id: str


class TestAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    datasource_id: int = Field(ge=1)
    conversation_id: str | None = Field(default=None, max_length=40)


class TestDiagnostics(BaseModel):
    """What only the admin sees: where the answer came from and why it failed."""

    principal: str
    datasource_id: int
    tables: list[str] = Field(default_factory=list)
    columns: dict[str, list[str]] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    guard: Literal["passed", "blocked", "not_reached"] = "not_reached"
    rule: str | None = None
    masked: list[str] = Field(default_factory=list)
    stage: str = ""
    timings: dict[str, int] = Field(default_factory=dict)
    # Model tokens the run used, failed runs included.
    input_tokens: int = 0
    output_tokens: int = 0
    query_run_id: int | None = None


class TestAskReply(BaseModel):
    # Exactly what the agent would receive, error included.
    agent: dict[str, Any]
    is_error: bool
    diagnostics: TestDiagnostics
