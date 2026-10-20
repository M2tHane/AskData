from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class Feedback(AuditFields, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_run_id", "query_run_created_at"],
            ["query_run.id", "query_run.created_at"],
            ondelete="CASCADE",
        ),
        Index("ix_feedback_team_status", "team_id", "status"),
    )
    query_run_id: Mapped[int]
    query_run_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    rating: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str | None] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_ref: Mapped[str | None] = mapped_column(String(100))
    example_id: Mapped[int | None] = mapped_column(ForeignKey("example_sql.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExampleSql(AuditFields, SoftDelete, Base):
    __tablename__ = "example_sql"
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    scope: Mapped[str] = mapped_column(String(20))
    owner_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    approved: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    review_comment: Mapped[str] = mapped_column(Text, default="")
