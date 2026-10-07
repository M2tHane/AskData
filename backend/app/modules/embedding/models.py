from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base
from app.modules.embedding.schemas import IndexKind


class EmbeddingItem(AuditFields, Base):
    __tablename__ = "embedding_item"
    __table_args__ = (
        UniqueConstraint("datasource_id", "kind", "ref_id", name="uq_embedding_ref"),
        Index("ix_embedding_datasource_kind", "datasource_id", "kind"),
        Index(
            "ix_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"))
    kind: Mapped[IndexKind] = mapped_column(String(20))
    ref_id: Mapped[int] = mapped_column(BigInteger)
    table_id: Mapped[int | None] = mapped_column(ForeignKey("meta_table.id"))
    column_id: Mapped[int | None] = mapped_column(ForeignKey("meta_column.id"))
    semantic_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_term.id"))
    metric_id: Mapped[int | None] = mapped_column(ForeignKey("metric.id"))
    example_id: Mapped[int | None] = mapped_column(ForeignKey("example_sql.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024).with_variant(JSON, "sqlite"))
    model_id: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(default=0)
    dirty: Mapped[bool] = mapped_column(default=True, index=True)
    revision: Mapped[int] = mapped_column(default=1)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
