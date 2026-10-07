from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class Metric(AuditFields, SoftDelete, Base):
    __tablename__ = "metric"
    __table_args__ = (
        Index("ix_metric_datasource_scope_team", "datasource_id", "scope", "owner_team_id"),
    )
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    scope: Mapped[str] = mapped_column(String(20))
    owner_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(20))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)

    name: Mapped[str] = mapped_column(String(200))
    sql_template: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
