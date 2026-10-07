from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class SemanticTerm(AuditFields, SoftDelete, Base):
    __tablename__ = "semantic_term"
    __table_args__ = (
        Index("ix_semantic_term_datasource_scope_team", "datasource_id", "scope", "owner_team_id"),
    )
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    scope: Mapped[str] = mapped_column(String(20))
    owner_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(20))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)

    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(BigInteger)
    target_table_id: Mapped[int | None] = mapped_column(ForeignKey("meta_table.id"))
    target_column_id: Mapped[int | None] = mapped_column(ForeignKey("meta_column.id"))
    enum_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
