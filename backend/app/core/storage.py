import asyncio
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
from typing import Protocol

from minio import Minio
from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode


class StoredObject(BaseModel):
    key: str
    size: int
    sha256: str


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def validate_key(key: str) -> str:
    path = PurePosixPath(key)
    if (
        not key
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in key
        or str(path) != key
        or any(ord(c) < 32 for c in key)
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR)
    return key


def team_segment(team_id: int | None) -> str:
    """Path segment for an owner team; admin-created uploads have no owner team."""
    if team_id is None:
        return "shared"
    if team_id < 1:
        raise AppError(ErrorCode.VALIDATION_ERROR)
    return f"team_{team_id}"


def object_key(
    team_id: int | None,
    datasource_id: int,
    version: int,
    filename: str,
    temporary: bool = False,
) -> str:
    if min(datasource_id, version) < 1 or PurePosixPath(filename).name != filename:
        raise AppError(ErrorCode.VALIDATION_ERROR)
    return validate_key(
        f"{'tmp' if temporary else 'uploads'}/{team_segment(team_id)}/ds_{datasource_id}"
        f"/v{version}/{filename}"
    )


class MinioStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        validate_key(key)
        try:
            await asyncio.to_thread(
                self.client.put_object,
                self.bucket,
                key,
                BytesIO(data),
                len(data),
                content_type=content_type,
            )
        except Exception:
            raise AppError(ErrorCode.STORAGE_UNAVAILABLE, http_status=503) from None
        return StoredObject(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    async def get(self, key: str) -> bytes:
        validate_key(key)

        def download() -> bytes:
            response = self.client.get_object(self.bucket, key)
            try:
                return bytes(response.read())
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(download)
        except Exception:
            raise AppError(ErrorCode.STORAGE_UNAVAILABLE, http_status=503) from None

    async def delete(self, key: str) -> None:
        validate_key(key)
        try:
            await asyncio.to_thread(self.client.remove_object, self.bucket, key)
        except Exception:
            raise AppError(ErrorCode.STORAGE_UNAVAILABLE, http_status=503) from None
