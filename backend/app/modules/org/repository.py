from sqlalchemy import func, select

from app.core.db import AsyncSession, BaseRepository
from app.modules.org.models import Team, User


class OrgRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.teams = BaseRepository(session, Team)
        self.users = BaseRepository(session, User)

    async def by_username(self, username: str) -> User | None:
        return await self.session.scalar(self.users._select().where(User.username == username))

    async def lock_admins(self) -> None:
        await self.session.scalars(
            select(User).where(User.role == "admin").order_by(User.id).with_for_update()
        )

    async def count_admins(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "admin", User.status == "active", User.deleted_at.is_(None))
            )
            or 0
        )

    async def team_has_users(self, team_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(User.id)
                .where(User.team_id == team_id, User.status == "active", User.deleted_at.is_(None))
                .limit(1)
            )
        )

    async def team_users(self, team_ids: list[int]) -> dict[int, list[User]]:
        rows = await self.session.scalars(
            select(User)
            .where(
                User.team_id.in_(team_ids),
                User.status == "active",
                User.deleted_at.is_(None),
            )
            .order_by(User.id)
        )
        grouped: dict[int, list[User]] = {}
        for row in rows:
            if row.team_id is not None:
                grouped.setdefault(row.team_id, []).append(row)
        return grouped

    async def list_users(
        self, offset: int, limit: int, *, free: bool, team_id: int | None
    ) -> tuple[list[User], int]:
        query = self.users._select()
        if free:
            query = query.where(User.team_id.is_(None), User.role != "admin")
        if team_id is not None:
            query = query.where(User.team_id == team_id)
        total = int(
            await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        )
        rows = await self.session.scalars(query.order_by(User.id).offset(offset).limit(limit))
        return list(rows), total

    async def active_team_ids(self) -> list[int]:
        rows = await self.session.scalars(
            select(Team.id).where(Team.deleted_at.is_(None), Team.status == "active")
        )
        return list(rows)

    async def count(self, kind: str) -> int:
        model = User if kind == "users" else Team
        return int(
            await self.session.scalar(
                select(func.count()).select_from(model).where(model.deleted_at.is_(None))
            )
            or 0
        )
