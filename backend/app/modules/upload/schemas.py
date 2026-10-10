from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.importer import ImportColumn


class ParseConfig(BaseModel):
    sheet: str | int = 0
    header_row: int | None = Field(default=1, ge=1)
    encoding: str | None = None
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    columns: list[ImportColumn] = Field(default_factory=list)
    table_name: str = "data"
    # Original sheet or file label kept as the table comment (names are ASCII-only).
    table_comment: str = Field(default="", max_length=200)


class PreviewRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(repr=False, max_length=70 * 1024 * 1024)
    datasource_id: int | None = Field(default=None, ge=1)
    datasource_name: str = Field(default="Uploaded data", max_length=200)
    owner_team_id: int | None = Field(default=None, ge=1)
    config: ParseConfig = Field(default_factory=ParseConfig)


class Preview(BaseModel):
    id: int
    datasource_id: int
    columns: list[ImportColumn]
    rows: list[list[Any]] = Field(repr=False)
    sheets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    config: ParseConfig


class ConfirmRequest(BaseModel):
    config: ParseConfig
    mode: Literal["overwrite", "append"] = "overwrite"


class UploadRetentionPolicy(BaseModel):
    retention_days: int = Field(default=90, ge=1, le=36500)
    batch_size: int = Field(default=100, ge=1, le=10000)


class UploadHistoryRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    datasource_id: int
    owner_team_id: int | None
    version: int
    filename: str
    file_key: str
    parse_config: dict[str, Any]
    status: str
    mode: str
    updated_at: datetime


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    datasource_id: int
    filename: str
    sha256: str
    size: int
    version: int
    mode: str
    status: str
    row_count: int
    task_id: str | None
    error: str | None
