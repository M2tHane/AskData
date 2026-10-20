"""Local login helper. Tokens go only to the explicitly selected private output file."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import AppError, ErrorCode
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import build_service, secret_box


async def login(output: Path) -> None:
    settings = get_settings()
    if (
        settings.database_url is None
        or settings.login_username is None
        or settings.login_password is None
    ):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "ASKDATA_DATABASE_URL, ASKDATA_LOGIN_USERNAME and ASKDATA_LOGIN_PASSWORD required",
        )
    database = Database(settings.database_url.get_secret_value())
    try:
        async with database.transaction() as session:
            pair = await build_service(session, settings).login(
                LoginRequest(username=settings.login_username, password=settings.login_password)
            )
            # Encrypt at rest too. No token or credential is ever emitted to stdout/logs.
            from pydantic import SecretStr

            encrypted = secret_box(settings).encrypt(SecretStr(pair.model_dump_json()))
            await asyncio.to_thread(write_tokens, output, encrypted)
    finally:
        await database.close()


def write_tokens(output: Path, encrypted: str) -> None:
    with open(output, "x", opener=__open_private) as stream:
        json.dump({"tokens_enc": encrypted}, stream)


def __open_private(path: str, flags: int) -> int:
    import os

    return os.open(path, flags, 0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(login(parser.parse_args().output))
