from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, UniqueConstraint
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
    row_filter: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
