from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PromotionInput(BaseModel):
    kind: Literal["semantic", "metric"]
    ref_id: int = Field(ge=1)


class ReviewInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=10000)


class PromotionView(PromotionInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    requested_by: int
    team_id: int
    reviewed_by: int | None
    status: Literal["pending", "approved", "rejected"]
    comment: str
    created_at: datetime
