from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class TeamTableGrant(AuditFields, Base):
    __tablename__ = "team_table_grant"
    __table_args__ = (UniqueConstraint("team_id", "table_id"),)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    table_id: Mapped[int] = mapped_column(ForeignKey("meta_table.id"))
    member_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)


class TeamColumnGrant(AuditFields, Base):
    __tablename__ = "team_column_grant"
    __table_args__ = (UniqueConstraint("team_id", "column_id"),)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    column_id: Mapped[int] = mapped_column(ForeignKey("meta_column.id"))
    member_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    mask_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class DatasourceTeamScope(AuditFields, Base):
    """Teams a datasource is visible to; uncustomized tables and columns inherit it."""

    __tablename__ = "datasource_team_scope"
    __table_args__ = (UniqueConstraint("datasource_id", "team_id"),)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))


class ScopeOverride(AuditFields, Base):
    """Marks a table or column whose team set was customized instead of inherited."""

    __tablename__ = "scope_override"
    __table_args__ = (UniqueConstraint("object_type", "object_id"),)
    object_type: Mapped[str] = mapped_column(String(10))
    object_id: Mapped[int] = mapped_column()


class ClientColumnGrant(AuditFields, Base):
    """An MCP connection's explicit allowlist. A table is visible when any column is granted;
    nothing is inherited, so new columns from a sync stay hidden until granted."""

    __tablename__ = "mcp_column_grant"
    __table_args__ = (UniqueConstraint("client_id", "column_id"),)
    client_id: Mapped[int] = mapped_column(ForeignKey("mcp_client.id"), index=True)
    column_id: Mapped[int] = mapped_column(ForeignKey("meta_column.id"))
    mask_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
