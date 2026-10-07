from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.audit import QueryLogRecord
from app.core.response import PageParams


class QueryMetadata(BaseModel):
    question: str = Field(default="", max_length=10000)
    conversation_id: int | None = None
    message_id: int | None = None
    model_id: str = ""
    generated_sql: str | None = None
    stage_timings: dict[str, int] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0, le=3)
    chart_spec: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)


class QueryView(QueryLogRecord):
    id: int
    created_at: datetime
    has_feedback: bool = False
    status: Literal["success", "blocked", "failed", "clarify"]


class QueryFilter(PageParams):
    since: datetime | None = None
    until: datetime | None = None
    user_id: int | None = Field(default=None, ge=1)
    datasource_id: int | None = Field(default=None, ge=1)
    status: Literal["success", "blocked", "failed", "clarify"] | None = None
    blocked_code: str | None = None
    has_feedback: bool | None = None
