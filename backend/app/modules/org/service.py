from typing import Annotated

from fastapi import Depends
from pydantic import SecretStr

from app.core.audit import AuditService
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, AuthenticationError, ErrorCode, PermissionDenied
from app.core.response import Page, PageParams
from app.core.security import hash_password, verify_password
from app.modules.auth.service import audit_for, get_session, require_admin
from app.modules.org.models import Team
from app.modules.org.repository import OrgRepository
from app.modules.org.schemas import (
    PersonRead,
    TeamCreate,
    TeamRead,
    TeamWrite,
    UserRead,
    UserUpdate,
    UserWrite,
)


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
        if row.role != Role.ADMIN and row.team_id is None:
            # Free employees wait in the pool until an admin assigns them to a team.
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

    async def active_team_ids(self) -> list[int]:
        """Internal use by permission defaults; not an API listing."""
        return await self.repository.active_team_ids()

    async def _team_reads(self, rows: list[Team]) -> list[TeamRead]:
        users = await self.repository.team_users([row.id for row in rows])
        reads = []
        for row in rows:
            members = users.get(row.id, [])
            leader = next((u for u in members if u.role == Role.LEADER), None)
            reads.append(
                TeamRead.model_validate(row).model_copy(
                    update={
                        "leader_id": leader.id if leader else None,
                        "leader_name": leader.username if leader else None,
                        "member_count": len(members),
                    }
                )
            )
        return reads

    async def _team(self, team_id: int) -> TeamRead:
        row = await self.repository.teams.get(team_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return (await self._team_reads([row]))[0]

    async def list_teams(self, page: PageParams, *, actor: CurrentUser) -> Page[TeamRead]:
        require_admin(actor)
        rows = await self.repository.teams.list(page.offset, page.page_size)
        return Page(
            items=await self._team_reads(rows),
            total=await self.repository.count("teams"),
            page=page.page,
            page_size=page.page_size,
        )

    async def people(self, *, actor: CurrentUser) -> list[PersonRead]:
        """Users whose queries and feedback the caller may review: everyone for admins,
        the own team for leaders."""
        if not actor.active or actor.role == Role.MEMBER:
            raise PermissionDenied()
        if actor.role == Role.LEADER and actor.team_id is None:
            return []
        team_id = None if actor.role == Role.ADMIN else actor.team_id
        rows, _ = await self.repository.list_users(0, 1000, free=False, team_id=team_id)
        return [PersonRead.model_validate(row) for row in rows]

    async def list_users(
        self,
        page: PageParams,
        *,
        actor: CurrentUser,
        free: bool = False,
        team_id: int | None = None,
    ) -> Page[UserRead]:
        require_admin(actor)
        rows, total = await self.repository.list_users(
            page.offset, page.page_size, free=free, team_id=team_id
        )
        return Page(
            items=[UserRead.model_validate(row) for row in rows],
            total=total,
            page=page.page,
            page_size=page.page_size,
        )

    async def create_team(self, data: TeamWrite, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        leader_id = data.leader_id if isinstance(data, TeamCreate) else None
        row = await self.repository.teams.create(data.model_dump(exclude={"leader_id"}), actor.id)
        await self.audit.record(actor, "org.team.create", ("team", str(row.id)))
        if leader_id is not None:
            await self._assign_leader(row.id, leader_id, actor)
        return await self._team(row.id)

    async def update_team(self, target_id: int, data: TeamWrite, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        row = await self.repository.teams.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if data.status != "active" and await self.repository.team_has_users(target_id):
            raise AppError(ErrorCode.CONFLICT)
        await self.repository.teams.update(row, data.model_dump())
        await self.audit.record(actor, "org.team.update", ("team", str(row.id)))
        return await self._team(row.id)

    async def _assign_leader(self, team_id: int, user_id: int, actor: CurrentUser) -> None:
        """New leader comes from this team or the free pool; the old leader becomes free."""
        user = await self.repository.users.get(user_id)
        if user is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if (
            user.role == Role.ADMIN
            or user.status != "active"
            or user.team_id not in {None, team_id}
        ):
            raise AppError(ErrorCode.CONFLICT)
        for member in (await self.repository.team_users([team_id])).get(team_id, []):
            if member.role == Role.LEADER and member.id != user.id:
                await self.repository.users.update(member, {"role": "member", "team_id": None})
        await self.repository.users.update(user, {"role": "leader", "team_id": team_id})
        await self.audit.record(
            actor, "org.team.leader", ("team", str(team_id)), {"user_id": user_id}
        )

    async def change_leader(self, team_id: int, user_id: int, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        await self.ensure_team(team_id)
        await self._assign_leader(team_id, user_id, actor)
        return await self._team(team_id)

    async def add_members(
        self, team_id: int, user_ids: list[int], *, actor: CurrentUser
    ) -> TeamRead:
        require_admin(actor)
        await self.ensure_team(team_id)
        for user_id in dict.fromkeys(user_ids):
            user = await self.repository.users.get(user_id)
            if user is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            if user.role != Role.MEMBER or user.team_id is not None or user.status != "active":
                raise AppError(ErrorCode.CONFLICT)
            await self.repository.users.update(user, {"team_id": team_id})
        await self.audit.record(
            actor, "org.team.members.add", ("team", str(team_id)), {"count": len(user_ids)}
        )
        return await self._team(team_id)

    async def remove_member(self, team_id: int, user_id: int, *, actor: CurrentUser) -> TeamRead:
        require_admin(actor)
        user = await self.repository.users.get(user_id)
        if user is None or user.team_id != team_id:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if user.role != Role.MEMBER:
            raise AppError(ErrorCode.CONFLICT)
        await self.repository.users.update(user, {"team_id": None})
        await self.audit.record(
            actor, "org.team.members.remove", ("team", str(team_id)), {"user_id": user_id}
        )
        return await self._team(team_id)

    async def _validate_user(self, role: Role, team_id: int | None) -> None:
        if role == Role.LEADER and team_id is None:
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
