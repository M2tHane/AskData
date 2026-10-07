from minio import Minio

from app.core.config import get_settings

settings = get_settings()
if settings.minio_access_key is None or settings.minio_secret_key is None:
    raise RuntimeError("Object storage credentials required")
client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key.get_secret_value(),
    secret_key=settings.minio_secret_key.get_secret_value(),
    secure=settings.minio_secure,
)
if not client.bucket_exists(settings.minio_bucket):
    client.make_bucket(settings.minio_bucket)
