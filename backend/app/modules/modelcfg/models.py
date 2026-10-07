from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class ModelConfiguration(AuditFields, SoftDelete, Base):
    __tablename__ = "model_config"
    type: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255))
    api_key_enc: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_dimension: Mapped[int | None] = mapped_column(nullable=True)


class TeamModelPreference(AuditFields, Base):
    __tablename__ = "team_model_pref"
    __table_args__ = (UniqueConstraint("team_id", "type"),)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    type: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[int] = mapped_column(ForeignKey("model_config.id"))
