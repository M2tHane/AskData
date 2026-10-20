"""Generate a private Compose environment; never emit credential values."""

import argparse
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def generate(path: Path) -> None:
    keys = (
        "META_DB_PASSWORD",
        "IMPORT_ADMIN_PASSWORD",
        "IMPORT_WRITER_PASSWORD",
        "IMPORT_READER_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "JWT_SECRET",
    )
    values = {key: secrets.token_hex(24) for key in keys}
    values["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    # Exclusive creation prevents clobbering an operator's existing credentials.
    with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
        file.write("".join(f"{key}={value}\n" for key, value in values.items()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    generate(parser.parse_args().path)
