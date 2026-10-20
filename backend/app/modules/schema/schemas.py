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
    added_tables: list[int] = Field(default_factory=list)
    added_columns: list[int] = Field(default_factory=list)
    # Found but held back until an administrator imports or describes them.
    pending_tables: list[int] = Field(default_factory=list)
    pending_columns: list[int] = Field(default_factory=list)


class SetupColumnWrite(BaseModel):
    column_id: int = Field(ge=1)
    # The column's semantic; equal to its database comment means "use the comment".
    description: str = Field(max_length=2000)


class SetupTableWrite(BaseModel):
    table_id: int = Field(ge=1)
    include: bool
    description: str | None = Field(default=None, max_length=2000)
    columns: list[SetupColumnWrite] = Field(default_factory=list, max_length=2000)


class SetupWrite(BaseModel):
    tables: list[SetupTableWrite] = Field(default_factory=list, max_length=5000)
    # False saves descriptions and the selection of not-yet-imported tables only;
    # True also imports the selected tables, and requires every one of their columns
    # to have a semantic.
    publish: bool = False


class SetupRead(BaseModel):
    datasource_id: int
    require_semantics: bool
    # Every table but missing ones: status active, pending (not imported yet) or excluded;
    # columns active or pending (waiting for a semantic).
    tables: list[TableRead]


class SetupResult(BaseModel):
    published: bool
    imported_tables: int = 0
    released_columns: int = 0
    excluded_tables: int = 0


class SetupSummary(BaseModel):
    datasource_id: int
    require_semantics: bool
    tables: int
    # Found and not imported yet / columns of imported tables waiting for a semantic.
    pending_tables: int
    pending_columns: int
    # Visible columns with neither a comment nor a description (older datasources).
    undocumented_columns: int


class DraftRequest(BaseModel):
    column_ids: list[int] = Field(min_length=1, max_length=300)


class DraftResult(BaseModel):
    # Proposed semantics by column id, for the administrator to review; never saved here.
    descriptions: dict[int, str]
