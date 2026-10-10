from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import AppError, ErrorCode
from app.core.scope import ScopeLevel, ScopeMixin

Reason = Literal[
    "metric_wrong",
    "table_wrong",
    "column_wrong",
    "empty_result",
    "result_wrong",
    "chart_wrong",
    "other",
]


class FeedbackInput(BaseModel):
    query_run_id: int = Field(ge=1)
    rating: Literal["up", "down"]
    reason_code: Reason | None = None
    comment: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def require_reason(self) -> "FeedbackInput":
        if self.rating == "down" and self.reason_code is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, "A reason is required", http_status=422)
        return self


class FeedbackView(FeedbackInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    team_id: int | None
    status: Literal["open", "handled"]
    snapshot: dict[str, Any]
    handled_by: int | None
    handled_at: datetime | None
    converted_ref: str | None
    expires_at: datetime
    created_at: datetime


class MyFeedback(BaseModel):
    """The caller's own rating of an answer, so the chat can show it after a reload."""

    model_config = ConfigDict(from_attributes=True)
    query_run_id: int
    rating: Literal["up", "down"]
    reason_code: str | None = None
    comment: str = ""


class ConversionInput(BaseModel):
    kind: Literal["eval", "example"]
    expected: dict[str, Any] = Field(default_factory=dict)
    corrected_sql: str | None = Field(default=None, max_length=50000)


class ExampleInput(BaseModel):
    datasource_id: int = Field(ge=1)
    scope: ScopeLevel = ScopeLevel.TEAM
    question: str = Field(min_length=1, max_length=10000)
    sql: str = Field(min_length=1, max_length=50000)


class ExampleView(ScopeMixin, ExampleInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: Literal["feedback", "manual"]
    approved: bool
    version: int


class ExampleReview(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=10000)
