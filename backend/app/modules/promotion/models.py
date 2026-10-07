from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base


class PromotionRequest(AuditFields, Base):
    __tablename__ = "promotion_request"
    __table_args__ = (Index("ix_promotion_team_status", "team_id", "status"),)
    kind: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[int] = mapped_column(BigInteger)
    semantic_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_term.id"))
    metric_id: Mapped[int | None] = mapped_column(ForeignKey("metric.id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
