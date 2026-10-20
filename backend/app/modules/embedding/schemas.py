from typing import Literal

from pydantic import BaseModel, Field

from app.core.scope import ScopeMixin

IndexKind = Literal["table", "column", "semantic", "metric", "example"]


class IndexReference(BaseModel):
    kind: IndexKind
    ref_id: int = Field(ge=1)
    datasource_id: int = Field(ge=1)
    actor_id: int = Field(ge=1)


class IndexDocument(ScopeMixin):
    kind: IndexKind
    ref_id: int = Field(ge=1)
    datasource_id: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=50000)
    references: dict[str, frozenset[str]] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    approved: bool = True


class IndexTaskInput(BaseModel):
    datasource_id: int = Field(ge=1)


class SearchInput(BaseModel):
    datasource_id: int = Field(ge=1)
    query: str = Field(min_length=1, max_length=10000)
    kinds: list[IndexKind] = Field(
        default_factory=lambda: list[IndexKind](
            ("table", "column", "semantic", "metric", "example")
        )
    )
    limit: int = Field(default=10, ge=1, le=100)
