from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptKind = Literal["system", "dialect"]


class PromptSummary(BaseModel):
    kind: PromptKind
    key: str
    label: str
    description: str
    seed_version: str
    variables: list[str]
    active_label: str
    revision_count: int
    updated_at: datetime | None = None


class RevisionMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    revision: int
    note: str
    is_active: bool
    author: str = ""
    created_at: datetime


class RevisionRead(RevisionMeta):
    content: str


class PromptDetail(BaseModel):
    kind: PromptKind
    key: str
    label: str
    description: str
    seed_version: str
    variables: list[str]
    seed_content: str
    active_label: str
    active_content: str
    revisions: list[RevisionMeta]


class RevisionWrite(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=200)
    activate: bool = True
