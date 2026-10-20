"""Run with uv run python scripts/create_admin.py; credentials come from ASKDATA_* env."""

import asyncio

from pydantic import SecretStr

from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import AppError, ErrorCode
from app.core.logging import LogEvent, configure_logging, logger
from app.modules.org.service import build_service


async def create_admin(username: str, password: SecretStr) -> int:
    settings = get_settings()
    if settings.database_url is None or len(password.get_secret_value()) < 12:
        raise AppError(ErrorCode.VALIDATION_ERROR)
    database = Database(settings.database_url.get_secret_value())
    try:
        async with database.transaction() as session:
            admin = await build_service(session).bootstrap_admin(username, password)
            return admin.id
    finally:
        await database.close()


async def main() -> None:
    settings = get_settings()
    if settings.bootstrap_password is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "ASKDATA_BOOTSTRAP_PASSWORD is required (at least 12 characters)",
        )
    configure_logging()
    identifier = await create_admin(settings.bootstrap_username, settings.bootstrap_password)
    logger.info(LogEvent.AUDIT, action="org.admin.bootstrap", user_id=identifier)


if __name__ == "__main__":
    asyncio.run(main())
