from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class QuotaInput(BaseModel):
    monthly_token_limit: int | None = Field(default=None, ge=0)
    daily_query_limit: int | None = Field(default=None, ge=0)


class QuotaView(QuotaInput):
    model_config = ConfigDict(from_attributes=True)
    team_id: int


class UsageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    date: date
    team_id: int | None
    user_id: int
    model_id: str
    calls: int
    tokens_in: int
    tokens_out: int
