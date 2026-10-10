from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class UploadJob(AuditFields, Base):
    __tablename__ = "upload_job"
    __table_args__ = (UniqueConstraint("datasource_id", "version"),)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    owner_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"), nullable=True)
    file_key: Mapped[str] = mapped_column(String(500))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    parse_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    mode: Mapped[str] = mapped_column(String(20), default="overwrite")
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="preview")
    row_count: Mapped[int] = mapped_column(default=0)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(100), nullable=True)
