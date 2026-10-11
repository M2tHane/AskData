from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class McpClient(AuditFields, SoftDelete, Base):
    """An MCP connection: an external agent with its own allowlist, overrides and limits."""

    __tablename__ = "mcp_client"
    public_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # The service account its queries, logs and usage are recorded under.
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    daily_call_limit: Mapped[int] = mapped_column(default=2000)
    default_rows: Mapped[int] = mapped_column(default=100)
    max_rows: Mapped[int] = mapped_column(default=1000)
    ask_timeout_seconds: Mapped[int] = mapped_column(default=60)
    allow_run_sql: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    daily_calls: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpApiKey(AuditFields, Base):
    __tablename__ = "mcp_api_key"
    client_id: Mapped[int] = mapped_column(ForeignKey("mcp_client.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    # SHA-256 of the full key; the key itself is shown once and never stored.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Set on rotation: the old key keeps working until then.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpItemOverride(AuditFields, Base):
    """A connection's change to one platform semantic term or metric: edit or hide.

    Deleting the row restores the platform default. Platform edits never touch it.
    """

    __tablename__ = "mcp_item_override"
    __table_args__ = (UniqueConstraint("client_id", "kind", "item_id"),)
    client_id: Mapped[int] = mapped_column(ForeignKey("mcp_client.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    item_id: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class McpConversation(AuditFields, Base):
    """The public conversation id an agent passes back, bound to one connection."""

    __tablename__ = "mcp_conversation"
    public_id: Mapped[str] = mapped_column(String(40), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("mcp_client.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversation.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The clarification the agent was last asked, answered by passing a choice to ask.
    pending_message_id: Mapped[int | None] = mapped_column(nullable=True)
