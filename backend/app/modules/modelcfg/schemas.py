from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

ModelKind = Literal["llm", "embedding", "rerank"]


class ModelParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    retries: int = Field(default=2, ge=0, le=3)
    # "responses" selects the OpenAI Responses protocol for reasoning models whose
    # thinking text would otherwise pollute chat/completions content.
    api: Literal["chat", "responses"] = "chat"
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class ModelWrite(BaseModel):
    type: ModelKind
    provider: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    base_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s@]+$")
    model_name: str = Field(min_length=1, max_length=255)
    api_key: SecretStr
    params: ModelParameters = Field(default_factory=ModelParameters)
    enabled: bool = True
    is_default: bool = False
    embedding_dimension: int | None = Field(default=None, ge=1, le=65536)


class ModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: ModelKind
    provider: str
    base_url: str | None
    model_name: str
    api_key: str = "********"
    params: ModelParameters
    enabled: bool
    is_default: bool
    embedding_dimension: int | None


class PreferenceWrite(BaseModel):
    model_id: int = Field(ge=1)


class PreferenceRead(BaseModel):
    team_id: int
    type: ModelKind
    model_id: int


class ModelTestResult(BaseModel):
    available: bool
