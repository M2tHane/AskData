from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.scope import ScopeLevel, ScopeMixin


class SemanticInput(BaseModel):
    datasource_id: int = Field(ge=1)
    scope: ScopeLevel = ScopeLevel.PERSONAL
    target_type: Literal["table", "column", "enum"]
    target_id: int = Field(ge=1)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=10000)
    enum_mapping: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SemanticView(ScopeMixin, SemanticInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_team_id: int | None
    owner_user_id: int | None
    status: Literal["private", "pending", "team", "active"]
    version: int
    created_at: datetime
    updated_at: datetime


class TargetReference(BaseModel):
    table: str
    column: str | None = None
