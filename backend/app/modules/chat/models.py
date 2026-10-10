from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class Conversation(AuditFields, SoftDelete, Base):
    __tablename__ = "conversation"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"), nullable=True)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    title: Mapped[str] = mapped_column(String(200))
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Message(AuditFields, Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        ForeignKeyConstraint(
            ["query_run_id", "query_run_created_at"],
            ["query_run.id", "query_run.created_at"],
            ondelete="SET NULL",
        ),
    )
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    query_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    query_run_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A structured pending question survives reconnects; clients cannot reset the one-round cap.
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_choices: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_answered: Mapped[bool] = mapped_column(default=False)
    parent_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("message.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Guarded SQL is retained for a repeat execution with fresh authorization, never raw results.
    normalized_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Chart configuration only; rows are re-read under fresh authorization when history is opened.
    chart_spec: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured explanation and model reasoning of an answer (JSON); never result rows.
    answer_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
