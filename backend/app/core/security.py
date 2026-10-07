import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, SecretStr

from app.core.deps import CurrentUser
from app.core.errors import AppError, AuthenticationError, ErrorCode

_passwords = PasswordHasher()


def hash_password(password: SecretStr) -> str:
    return _passwords.hash(password.get_secret_value())


def verify_password(password: SecretStr, hashed: str) -> bool:
    try:
        return _passwords.verify(hashed, password.get_secret_value())
    except (VerificationError, InvalidHashError):
        return False


class TokenClaims(BaseModel):
    sub: str
    jti: str
    kind: Literal["access"]
    exp: int
    iat: int
    iss: str
    aud: str


class TokenService:
    def __init__(self, secret: SecretStr, issuer: str = "askdata", minutes: int = 15) -> None:
        if len(secret.get_secret_value()) < 32:
            raise ValueError("JWT signing secret is too short")
        self._secret = secret
        self.issuer = issuer
        self.minutes = minutes

    def issue_access(self, user_id: int) -> str:
        now = datetime.now(UTC)
        claims = TokenClaims(
            sub=str(user_id),
            jti=str(uuid4()),
            kind="access",
            exp=int((now + timedelta(minutes=self.minutes)).timestamp()),
            iat=int(now.timestamp()),
            iss=self.issuer,
            aud="askdata-api",
        )
        return jwt.encode(claims.model_dump(), self._secret.get_secret_value(), algorithm="HS256")

    def verify_access(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.issuer,
                audience="askdata-api",
                options={"require": ["exp", "iat", "sub", "jti", "kind", "iss", "aud"]},
            )
            claims = TokenClaims.model_validate(payload)
            if not claims.sub.isdigit() or int(claims.sub) <= 0:
                raise AuthenticationError()
            return claims
        except jwt.ExpiredSignatureError:
            raise AppError(ErrorCode.TOKEN_EXPIRED, http_status=401) from None
        except (jwt.PyJWTError, ValueError):
            raise AuthenticationError() from None


class RefreshRecord(BaseModel):
    user_id: int
    token_hash: str
    expires_at: datetime
    revoked: bool = False


class RefreshStore(Protocol):
    async def save(self, record: RefreshRecord) -> None: ...
    async def consume(self, token_hash: str, now: datetime) -> RefreshRecord | None:
        """Atomically revoke and return an unexpired token; replay returns None."""
        ...


class RefreshTokens:
    def __init__(self, store: RefreshStore, days: int = 7) -> None:
        self.store = store
        self.days = days

    async def issue(self, user_id: int) -> SecretStr:
        token = SecretStr(secrets.token_urlsafe(48))
        await self.store.save(
            RefreshRecord(
                user_id=user_id,
                token_hash=self.digest(token),
                expires_at=datetime.now(UTC) + timedelta(days=self.days),
            )
        )
        return token

    @staticmethod
    def digest(token: SecretStr) -> str:
        return hashlib.sha256(token.get_secret_value().encode()).hexdigest()

    async def revoke(self, token: SecretStr) -> None:
        await self.store.consume(self.digest(token), datetime.now(UTC))

    async def rotate(self, token: SecretStr) -> tuple[int, SecretStr]:
        record = await self.store.consume(self.digest(token), datetime.now(UTC))
        if record is None:
            raise AuthenticationError()
        return record.user_id, await self.issue(record.user_id)


class AuthenticationProvider(Protocol):
    async def authenticate(self, username: str, password: SecretStr) -> CurrentUser: ...
