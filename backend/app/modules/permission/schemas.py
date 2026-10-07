from pydantic import BaseModel, Field

from app.core.masking import MaskRule
from app.core.permission import RowFilter


class ColumnGrantWrite(BaseModel):
    column_id: int = Field(ge=1)
    member_visible: bool = False
    mask_rule: MaskRule | None = None
    row_filters: list[RowFilter] = Field(default_factory=list)


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


class PublicTableScope(BaseModel):
    columns: list[str]
    masks: dict[str, MaskRule] = Field(default_factory=dict)


class PublicScope(BaseModel):
    datasource_id: int
    tables: dict[str, PublicTableScope]
