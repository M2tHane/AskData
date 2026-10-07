from typing import Annotated

from fastapi import Depends
from pydantic import SecretStr

from app.core.audit import AuditService
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, AuthenticationError, ErrorCode
from app.core.response import Page, PageParams
from app.core.security import hash_password, verify_password
from app.modules.auth.service import audit_for, get_session, require_admin
from app.modules.org.repository import OrgRepository
from app.modules.org.schemas import TeamRead, TeamWrite, UserRead, UserUpdate, UserWrite


class OrgService:
    def __init__(self, repository: OrgRepository, audit: AuditService) -> None:
        self.repository, self.audit = repository, audit

    async def ensure_team(self, team_id: int) -> TeamRead:
        row = await self.repository.teams.get(team_id)
        if row is None or row.status != "active":
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return TeamRead.model_validate(row)

    async def user(self, user_id: int) -> UserRead:
        row = await self.repository.users.get(user_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return UserRead.model_validate(row)

    async def identity(self, user_id: int) -> CurrentUser:
        row = await self.repository.users.get(user_id)
        if row is None or row.status != "active":
            raise AuthenticationError()
        if row.team_id is not None:
            team = await self.repository.teams.get(row.team_id)
            if team is None or team.status != "active":
                raise AuthenticationError()
        return CurrentUser(id=row.id, role=Role(row.role), team_id=row.team_id)

    async def authenticate(self, username: str, password: SecretStr) -> CurrentUser:
        row = await self.repository.by_username(username)
        if row is None or not verify_password(password, row.password_hash):
            raise AuthenticationError()
        return await self.identity(row.id)

    async def list_teams(self, page: PageParams, *, actor: CurrentUser) -> Page[TeamRead]:
        require_admin(actor)
        rows = await self.repository.teams.list(page.offset, page.page_size)
        return Page(
            items=[TeamRead.model_validate(row) for row in rows],
            total=await self.repository.count("teams"),
            page=page.page,
            page_size=page.page_size,
        )

    async def list_users(self, page: PageParams, *, actor: CurrentUser) -> Page[UserRead]:
        require_admin(actor)
        rows = await self.repository.users.list(page.offset, page.page_size)
        return Page(
            items=[UserRead.model_validate(row) for row in rows],
            total=await self.repository.count("users"),
            page=page.page,
            page_size=page.page_size,
        )

    async def create_team(self, data: TeamWrite, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        row = await self.repository.teams.create(data.model_dump(), actor.id)
        await self.audit.record(actor, "org.team.create", ("team", str(row.id)))
        return TeamRead.model_validate(row)

    async def update_team(self, target_id: int, data: TeamWrite, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        row = await self.repository.teams.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if data.status != "active" and await self.repository.team_has_users(target_id):
            raise AppError(ErrorCode.CONFLICT)
        await self.repository.teams.update(row, data.model_dump())
        await self.audit.record(actor, "org.team.update", ("team", str(row.id)))
        return TeamRead.model_validate(row)

    async def _validate_user(self, role: Role, team_id: int | None) -> None:
        if role != Role.ADMIN and team_id is None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if team_id is not None:
            await self.ensure_team(team_id)

    async def create_user(self, data: UserWrite, *, actor: CurrentUser) -> UserRead:
        require_admin(actor)
        await self._validate_user(data.role, data.team_id)
        if await self.repository.by_username(data.username):
            raise AppError(ErrorCode.CONFLICT)
        values = data.model_dump(exclude={"password"})
        values["password_hash"] = hash_password(data.password)
        row = await self.repository.users.create(values, actor.id)
        await self.audit.record(actor, "org.user.create", ("user", str(row.id)))
        return UserRead.model_validate(row)

    async def update_user(
        self, target_id: int, data: UserUpdate, *, actor: CurrentUser
    ) -> UserRead:
        require_admin(actor)
        await self.repository.lock_admins()
        await self._validate_user(data.role, data.team_id)
        row = await self.repository.users.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if target_id == actor.id and (data.role != Role.ADMIN or data.status != "active"):
            raise AppError(ErrorCode.CONFLICT)
        if (
            row.role == Role.ADMIN
            and row.status == "active"
            and (data.role != Role.ADMIN or data.status != "active")
            and await self.repository.count_admins() <= 1
        ):
            raise AppError(ErrorCode.CONFLICT)
        values = data.model_dump(exclude={"password"})
        if data.password:
            values["password_hash"] = hash_password(data.password)
        await self.repository.users.update(row, values)
        await self.audit.record(actor, "org.user.update", ("user", str(row.id)))
        return UserRead.model_validate(row)

    async def bootstrap_admin(self, username: str, password: SecretStr) -> UserRead:
        if await self.repository.count_admins():
            raise AppError(ErrorCode.CONFLICT)
        # The first identity is created only by the local setup command, never an HTTP endpoint.
        values = {
            "username": username,
            "password_hash": hash_password(password),
            "role": "admin",
            "status": "active",
            "team_id": None,
        }
        row = await self.repository.users.create(values, 0)
        row.created_by = row.id
        actor = CurrentUser(id=row.id, role=Role.ADMIN)
        await self.audit.record(actor, "org.admin.bootstrap", ("user", str(row.id)))
        return UserRead.model_validate(row)


def build_service(session: AsyncSession) -> OrgService:
    return OrgService(OrgRepository(session), audit_for(session))


def get_service(
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> OrgService:
    return build_service(session)
