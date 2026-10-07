from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base, SoftDelete


class MetaTable(AuditFields, SoftDelete, Base):
    __tablename__ = "meta_table"
    __table_args__ = (UniqueConstraint("datasource_id", "schema_name", "name"),)
    datasource_id: Mapped[int] = mapped_column(ForeignKey("datasource.id"), index=True)
    schema_name: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetaColumn(AuditFields, SoftDelete, Base):
    __tablename__ = "meta_column"
    __table_args__ = (UniqueConstraint("table_id", "name"),)
    table_id: Mapped[int] = mapped_column(ForeignKey("meta_table.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False)
    ordinal: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="active")


class MetaRelation(AuditFields, SoftDelete, Base):
    __tablename__ = "meta_relation"
    from_column_id: Mapped[int] = mapped_column(ForeignKey("meta_column.id"))
    to_column_id: Mapped[int] = mapped_column(ForeignKey("meta_column.id"))
    source: Mapped[str] = mapped_column(String(20), default="fk")
