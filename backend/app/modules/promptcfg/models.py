from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class PromptRevision(AuditFields, Base):
    """Immutable published text. At most one revision per (kind, key) is active."""

    __tablename__ = "prompt_revision"
    __table_args__ = (UniqueConstraint("kind", "key", "revision"),)
    kind: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
