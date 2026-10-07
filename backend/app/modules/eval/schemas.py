from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ErrorCode
from app.core.sql.executor import QueryResult

SAFETY_CATEGORIES = frozenset({"violation", "out_of_scope", "injection"})


class FeedbackOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_run_id: int = Field(ge=1)
    query_run_created_at: datetime | None = None
    datasource_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    team_id: int | None = Field(default=None, ge=1)
    converted_by: int = Field(ge=1)
    timezone: str = "Asia/Shanghai"


class FeedbackSnapshotInput(BaseModel):
    # The QueryLogService snapshot contains many fields; retain only the replay origin.
    model_config = ConfigDict(extra="ignore")
    id: int = Field(ge=1)
    datasource_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    team_id: int | None = Field(default=None, ge=1)
    question: str = Field(min_length=1, max_length=10000)
    created_at: datetime | None = None


type FeedbackExpectedCell = str | bool | int | Annotated[float, Field(allow_inf_nan=False)] | None


class FeedbackCaseExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal[
        "single_table",
        "join",
        "analytics",
        "time",
        "semantic",
        "chart",
        "clarify",
        "violation",
        "out_of_scope",
        "injection",
        "empty",
        "upload",
    ] = "single_table"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    expected_sql: str | None = Field(default=None, min_length=1, max_length=50000)
    expected_values: list[list[FeedbackExpectedCell]] | None = Field(default=None, repr=False)
    # Existing feedback clients may provide row dictionaries instead of values.
    rows: list[dict[str, FeedbackExpectedCell]] | None = Field(default=None, repr=False)
    expected_columns: list[str] | None = Field(default=None, min_length=1, max_length=100)
    result_mode: Literal["unordered", "ordered", "scalar"] = "unordered"
    expect_status: Literal["success", "blocked", "clarify"] = "success"
    expect_code: str | None = None
    candidate_sql: str | None = Field(default=None, max_length=50000)
    expected_chart_types: list[Literal["bar", "line", "pie", "scatter"]] = Field(
        default_factory=list
    )
    expected_semantic_ids: list[str] = Field(default_factory=list)
    expected_metric_ids: list[str] = Field(default_factory=list)
    expected_time_range: dict[str, str] | None = None
    expected_clarification_options: list[str] = Field(default_factory=list)
    expect_empty: bool = False
    notes: str = Field(default="", max_length=2000)


class EvalCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: str
    difficulty: Literal["easy", "medium", "hard"]
    datasource: str
    as_user: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    question: str
    expected_sql: str | None = None
    result_mode: Literal["unordered", "ordered", "scalar"] = "unordered"
    expect_status: Literal["success", "blocked", "clarify"] = "success"
    expect_code: ErrorCode | None = None
    expected_chart_types: list[str] = Field(default_factory=list)
    source: Literal["manual", "feedback"] = "manual"
    notes: str = ""
    candidate_sql: str | None = None
    expected_semantic_ids: list[str] = Field(default_factory=list)
    expected_metric_ids: list[str] = Field(default_factory=list)
    expected_time_range: dict[str, str] | None = None
    expected_clarification_options: list[str] = Field(default_factory=list)
    expect_empty: bool = False
    expected_values: list[list[Any]] | None = None
    expected_columns: list[str] | None = None
    injection_context: dict[str, Any] | None = None
    feedback_origin: FeedbackOrigin | None = None


class PipelineOutcome(BaseModel):
    status: Literal["success", "blocked", "clarify", "failed"]
    sql: str | None = None
    result: QueryResult | None = Field(default=None, repr=False)
    blocked_code: ErrorCode | None = None
    # Must come from query logs/executor instrumentation, not from model text.
    executed_queries: int = Field(ge=0)
    retries: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    message: str = ""
    clarification_options: list[str] = Field(default_factory=list)
    chart_type: str | None = None
    chart_valid: bool = False
    semantic_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    time_range: dict[str, str] | None = None


class Evaluation(BaseModel):
    case_id: str
    category: str
    passed: bool
    reason: str
    actual_sql: str | None = None
    actual_result_digest: str | None = None
    blocked_code: ErrorCode | None = None
    retries: int = 0
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class RunRequest(BaseModel):
    model_id: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    case_ids: list[str] = Field(default_factory=list)


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    model_id: str
    prompt_versions: dict[str, str]
    config: dict[str, Any]
    status: str
    task_id: str | None
    summary: dict[str, Any]


class RunComparison(BaseModel):
    before_id: int
    after_id: int
    new_failures: list[str]
    fixed: list[str]
    added: list[str]
    removed: list[str]
