from pydantic import BaseModel, Field

from app.core.masking import MaskRule


class ColumnGrantWrite(BaseModel):
    column_id: int = Field(ge=1)
    member_visible: bool = False
    mask_rule: MaskRule | None = None


class TableGrantWrite(BaseModel):
    table_id: int = Field(ge=1)
    member_visible: bool = False
    columns: list[ColumnGrantWrite] = Field(default_factory=list)


class TeamGrantsWrite(BaseModel):
    tables: list[TableGrantWrite] = Field(default_factory=list)


class MemberTableWrite(BaseModel):
    table_id: int = Field(ge=1)
    column_ids: list[int] = Field(default_factory=list)


class MemberGrantsWrite(BaseModel):
    tables: list[MemberTableWrite] = Field(default_factory=list)


class GrantRead(TeamGrantsWrite):
    datasource_id: int
    team_id: int


class ClientColumnWrite(BaseModel):
    column_id: int = Field(ge=1)
    mask_rule: MaskRule | None = None


class ClientGrantsWrite(BaseModel):
    columns: list[ClientColumnWrite] = Field(default_factory=list)


class ClientGrantsRead(ClientGrantsWrite):
    datasource_id: int
    client_id: int


class PublicTableScope(BaseModel):
    columns: list[str]
    masks: dict[str, MaskRule] = Field(default_factory=dict)


class PublicScope(BaseModel):
    datasource_id: int
    tables: dict[str, PublicTableScope]


class ColumnVisibility(BaseModel):
    column_id: int
    custom: bool
    team_ids: list[int]


class TableVisibility(BaseModel):
    table_id: int
    custom: bool
    team_ids: list[int]
    columns: list[ColumnVisibility]


class VisibilityRead(BaseModel):
    datasource_id: int
    team_ids: list[int]
    custom: bool
    tables: list[TableVisibility]


class DatasourceVisibilityWrite(BaseModel):
    team_ids: list[int] = Field(default_factory=list, max_length=500)


class ColumnScopeWrite(BaseModel):
    column_id: int = Field(ge=1)
    team_ids: list[int] = Field(default_factory=list, max_length=500)


class TableScopeWrite(BaseModel):
    table_id: int = Field(ge=1)
    # custom=False keeps the table inheriting; its team_ids are then ignored.
    custom: bool = False
    team_ids: list[int] = Field(default_factory=list, max_length=500)
    columns: list[ColumnScopeWrite] = Field(default_factory=list, max_length=5000)


class FullVisibilityWrite(BaseModel):
    """The whole visibility state in one atomic save; unlisted objects inherit."""

    team_ids: list[int] = Field(default_factory=list, max_length=500)
    tables: list[TableScopeWrite] = Field(default_factory=list, max_length=5000)


class ObjectVisibilityWrite(BaseModel):
    inherit: bool = False
    team_ids: list[int] = Field(default_factory=list, max_length=500)
