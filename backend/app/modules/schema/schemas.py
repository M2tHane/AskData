from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ColumnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    table_id: int
    name: str
    data_type: str
    comment: str
    description: str
    is_pk: bool
    ordinal: int
    status: str


class TableRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    datasource_id: int
    schema_name: str
    name: str
    comment: str
    description: str
    status: str
    synced_at: datetime
    columns: list[ColumnRead] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class RelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_column_id: int
    to_column_id: int
    source: str


class DescriptionWrite(BaseModel):
    description: str = Field(max_length=10000)


class SyncResult(BaseModel):
    datasource_id: int
    removed_tables: list[int]
    removed_columns: list[int]
    table_count: int
