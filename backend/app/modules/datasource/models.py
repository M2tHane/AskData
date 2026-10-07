from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class DatasourceRecord(AuditFields, SoftDelete, Base):
    __tablename__ = "datasource"
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int]
    database: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    secret_enc: Mapped[str] = mapped_column(Text)
    schema_name: Mapped[str] = mapped_column(String(255), default="public")
    dialect: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")
    tls: Mapped[bool] = mapped_column(Boolean, default=True)
    read_only_account: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"), nullable=True)
    upload_initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_publications: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
