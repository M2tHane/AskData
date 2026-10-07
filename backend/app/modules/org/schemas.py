from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.deps import Role


class TeamWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    status: Literal["active", "disabled"] = "active"


class TeamRead(TeamWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int


class UserWrite(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[\w.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=1024)
    role: Role
    team_id: int | None = Field(default=None, ge=1)
    status: Literal["active", "disabled"] = "active"


class UserUpdate(BaseModel):
    role: Role
    team_id: int | None = Field(default=None, ge=1)
    status: Literal["active", "disabled"] = "active"
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: Role
    team_id: int | None
    status: str
