from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class EvalCase(AuditFields, Base):
    __tablename__ = "eval_case"
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(50))
    difficulty: Mapped[str] = mapped_column(String(20))
    question: Mapped[str]
    expected_sql: Mapped[str | None]
    expected_result: Mapped[list[list[Any]] | None] = mapped_column(JSON, nullable=True)
    expect_blocked: Mapped[bool]
    expected_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)


class EvalRun(AuditFields, Base):
    __tablename__ = "eval_run"
    model_id: Mapped[str] = mapped_column(String(100))
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    case_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class EvalResult(AuditFields, Base):
    __tablename__ = "eval_result"
    __table_args__ = (UniqueConstraint("run_id", "case_id"),)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_run.id", ondelete="CASCADE"))
    case_id: Mapped[int] = mapped_column(ForeignKey("eval_case.id"))
    passed: Mapped[bool]
    actual_sql: Mapped[str | None]
    actual_result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
