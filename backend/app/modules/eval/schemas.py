from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ErrorCode
from app.core.sql.executor import QueryResult

SAFETY_CATEGORIES = frozenset({"violation", "out_of_scope", "injection"})


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
    injection_context: dict[str, Any] | None = None


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
