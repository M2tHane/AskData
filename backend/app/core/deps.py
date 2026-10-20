from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Protocol

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, model_validator

from app.core.errors import AuthenticationError, PermissionDenied


class Role(StrEnum):
    ADMIN = "admin"
    LEADER = "leader"
    MEMBER = "member"


class CurrentUser(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    role: Role
    team_id: int | None = None
    active: bool = True
    # Set for an MCP connection's service account: its grants, not a team's, define the scope.
    client_id: int | None = None

    @model_validator(mode="after")
    def validate_team(self) -> "CurrentUser":
        if self.client_id is not None:
            if self.role != Role.MEMBER or self.team_id is not None:
                raise ValueError("A service account is a member without a team")
        elif self.role != Role.ADMIN and self.team_id is None:
            raise ValueError("Non-admin users require a team")
        return self


class IdentityResolver(Protocol):
    async def resolve(self, token: str) -> CurrentUser: ...


_bearer = HTTPBearer(auto_error=False)


def identity_resolver() -> IdentityResolver:
    # Override this dependency when auth is wired; never accept an unauthenticated default.
    raise AuthenticationError()


async def access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None:
        raise AuthenticationError()
    return credentials.credentials


async def current_user(
    token: Annotated[str, Depends(access_token)],
    resolver: Annotated[IdentityResolver, Depends(identity_resolver)],
) -> CurrentUser:
    user = await resolver.resolve(token)
    if not user.active:
        raise AuthenticationError()
    return user


def require_role(*roles: Role) -> Callable[..., CurrentUser]:
    def dependency(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise PermissionDenied()
        return user

    return dependency
