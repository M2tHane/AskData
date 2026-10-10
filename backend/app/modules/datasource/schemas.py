from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DatasourceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["mysql", "postgresql", "kingbase", "gaussdb", "clickhouse"]
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr
    schema_name: str = Field(default="public", min_length=1, max_length=255)
    tls: bool = True


class DatasourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    host: str
    port: int
    database: str
    username: str
    schema_name: str
    tls: bool
    status: str
    read_only_account: bool
    password: str = "********"
    warning: str | None = None
    owner_team_id: int | None = None


class DatasourceChoice(BaseModel):
    id: int
    name: str
    type: str
    dialect: str


class UploadPublication(BaseModel):
    datasource_id: int = Field(ge=1)
    owner_team_id: int | None = Field(default=None, ge=1)
    completed: bool = False
    initialize_owner_grants: bool = False


class DatasourceStatus(BaseModel):
    status: Literal["active", "disabled"]
