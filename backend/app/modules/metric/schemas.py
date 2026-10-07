from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.scope import ScopeLevel, ScopeMixin


class MetricInput(BaseModel):
    datasource_id: int = Field(ge=1)
    scope: ScopeLevel = ScopeLevel.PERSONAL
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=10000)
    sql_template: str = Field(min_length=1, max_length=50000)
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class MetricView(ScopeMixin, MetricInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_team_id: int | None
    owner_user_id: int | None
    status: Literal["private", "pending", "team", "active"]
    version: int
    last_validated_at: datetime
    created_at: datetime
    updated_at: datetime
