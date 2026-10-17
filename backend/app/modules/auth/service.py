from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from fastapi import Request
from pydantic import SecretStr

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, AuthenticationError, ErrorCode, PermissionDenied
from app.core.security import RefreshTokens, TokenService
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import LoginRequest, TokenPair

if TYPE_CHECKING:
    from app.modules.org.service import OrgService


def require_admin(actor: CurrentUser) -> None:
    if not actor.active or actor.role != Role.ADMIN:
        raise PermissionDenied()


def settings_for(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def secret_box(settings: Settings) -> SecretBox:
    if settings.encryption_key is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return SecretBox(settings.encryption_key)


def audit_for(session: AsyncSession) -> AuditService:
    return AuditService(DatabaseAuditStore(session))


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        org: "OrgService",
        tokens: TokenService,
        audit: AuditService,
        refresh_days: int = 7,
    ) -> None:
        self.repository, self.org, self.tokens, self.audit = repository, org, tokens, audit
        self.refresh = RefreshTokens(repository, refresh_days)

    async def resolve(self, token: str) -> CurrentUser:
        return await self.org.identity(int(self.tokens.verify_access(token).sub))

    async def login(self, data: LoginRequest) -> TokenPair:
        actor = await self.org.authenticate(data.username, data.password)
        refresh = await self.refresh.issue(actor.id)
        await self.audit.record(actor, "auth.login", ("user", str(actor.id)))
        return self._pair(actor.id, refresh)

    def _pair(self, user_id: int, refresh: SecretStr) -> TokenPair:
        return TokenPair(
            access_token=self.tokens.issue_access(user_id),
            refresh_token=refresh.get_secret_value(),
            expires_in=self.tokens.minutes * 60,
        )

    async def rotate(self, token: SecretStr) -> TokenPair:
        record = await self.repository.consume(RefreshTokens.digest(token), datetime.now(UTC))
        if record is None:
            raise AuthenticationError()
        actor = await self.org.identity(record.user_id)
        refresh = await self.refresh.issue(actor.id)
        await self.audit.record(actor, "auth.refresh", ("user", str(actor.id)))
        return self._pair(actor.id, refresh)

    async def logout(self, token: SecretStr, *, actor: CurrentUser) -> None:
        record = await self.repository.consume(RefreshTokens.digest(token), datetime.now(UTC))
        if record is None or record.user_id != actor.id:
            raise AuthenticationError()
        await self.audit.record(actor, "auth.logout", ("user", str(actor.id)))


def build_service(session: AsyncSession, settings: Settings | None = None) -> AuthService:
    from app.modules.org.service import build_service as build_org

    settings = settings or get_settings()
    if settings.jwt_secret is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return AuthService(
        AuthRepository(session),
        build_org(session),
        TokenService(settings.jwt_secret, settings.jwt_issuer, settings.access_token_minutes),
        audit_for(session),
        settings.refresh_token_days,
    )


def get_service() -> AuthService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
