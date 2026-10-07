from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class Team(AuditFields, SoftDelete, Base):
    __tablename__ = "team"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")


class User(AuditFields, SoftDelete, Base):
    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("role IN ('admin','leader','member')", name="ck_user_role"),
        CheckConstraint("role = 'admin' OR team_id IS NOT NULL", name="ck_user_team"),
    )
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
