from pydantic import BaseModel, Field

from app.core.errors import ErrorCode


class Envelope[T](BaseModel):
    code: ErrorCode = ErrorCode.OK
    message: str = "Success"
    data: T | None = None


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(ge=0)
    page: int
    page_size: int
