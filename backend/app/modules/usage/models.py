from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class UsageDaily(AuditFields, Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("date", "user_id", "model_id", name="uq_usage_daily"),
        Index("ix_usage_team_date", "team_id", "date"),
    )
    date: Mapped[date] = mapped_column(Date)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    model_id: Mapped[str] = mapped_column(String(100))
    calls: Mapped[int] = mapped_column(default=0)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)


class TeamQuota(AuditFields, Base):
    __tablename__ = "team_quota"
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), unique=True)
    monthly_token_limit: Mapped[int | None]
    daily_query_limit: Mapped[int | None]
    daily_date: Mapped[date | None] = mapped_column(Date)
    daily_calls: Mapped[int] = mapped_column(default=0)


class UsageReservation(AuditFields, Base):
    __tablename__ = "usage_reservation"
    reservation_id: Mapped[str] = mapped_column(String(36), unique=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
