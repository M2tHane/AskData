from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.errors import AppError, ErrorCode


class SecretBox:
    def __init__(self, key: SecretStr) -> None:
        self._cipher = Fernet(key.get_secret_value().encode())

    def encrypt(self, value: SecretStr) -> str:
        return self._cipher.encrypt(value.get_secret_value().encode()).decode()

    def decrypt(self, encrypted: str) -> SecretStr:
        try:
            return SecretStr(self._cipher.decrypt(encrypted.encode()).decode())
        except (InvalidToken, ValueError, UnicodeError):
            raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500) from None

    @staticmethod
    def mask(value: object = None) -> str:
        return "********" if value else ""
