from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASKDATA_", extra="ignore")
    environment: str = "development"
    database_url: SecretStr | None = None
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    jwt_secret: SecretStr | None = None
    encryption_key: SecretStr | None = None
    jwt_issuer: str = "askdata"
    access_token_minutes: int = Field(default=15, ge=1)
    refresh_token_days: int = Field(default=7, ge=1)
    query_timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_query_rows: int = Field(default=10000, ge=1, le=100000)
    max_query_cost: float = Field(default=100000, gt=0)
    retention_days: int = Field(default=90, ge=1)
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket: str = "askdata"
    minio_secure: bool = True
    bootstrap_username: str = "admin"
    bootstrap_password: SecretStr | None = None
    login_username: str | None = None
    login_password: SecretStr | None = None

    def require_secrets(self) -> None:
        if self.jwt_secret is None or self.encryption_key is None:
            raise ValueError("ASKDATA_JWT_SECRET and ASKDATA_ENCRYPTION_KEY are required")
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
