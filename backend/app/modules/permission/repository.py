from sqlalchemy import select, update

from app.core.db import AsyncSession
from app.modules.permission.models import TeamColumnGrant, TeamTableGrant
from app.modules.permission.schemas import TeamGrantsWrite


class PermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grants(
        self, team_id: int | None, table_ids: list[int], column_ids: list[int]
    ) -> tuple[list[TeamTableGrant], list[TeamColumnGrant]]:
        tables = list(
            await self.session.scalars(
                select(TeamTableGrant).where(
                    TeamTableGrant.team_id == team_id,
                    TeamTableGrant.table_id.in_(table_ids),
                    TeamTableGrant.valid.is_(True),
                )
            )
        )
        columns = list(
            await self.session.scalars(
                select(TeamColumnGrant).where(
                    TeamColumnGrant.team_id == team_id,
                    TeamColumnGrant.column_id.in_(column_ids),
                    TeamColumnGrant.valid.is_(True),
                )
            )
        )
        return tables, columns

    async def replace(
        self,
        team_id: int,
        table_ids: list[int],
        column_ids: list[int],
        data: TeamGrantsWrite,
        actor_id: int,
    ) -> None:
        await self.expire(table_ids, column_ids, team_id)
        for table in data.tables:
            row = await self.session.scalar(
                select(TeamTableGrant).where(
                    TeamTableGrant.team_id == team_id, TeamTableGrant.table_id == table.table_id
                )
            )
            if row is None:
                row = TeamTableGrant(team_id=team_id, table_id=table.table_id, created_by=actor_id)
                self.session.add(row)
            row.valid, row.member_visible = True, table.member_visible
            for column in table.columns:
                col = await self.session.scalar(
                    select(TeamColumnGrant).where(
                        TeamColumnGrant.team_id == team_id,
                        TeamColumnGrant.column_id == column.column_id,
                    )
                )
                if col is None:
                    col = TeamColumnGrant(
                        team_id=team_id, column_id=column.column_id, created_by=actor_id
                    )
                    self.session.add(col)
                col.valid, col.member_visible = True, column.member_visible
                col.mask_rule = (
                    column.mask_rule.model_dump(mode="json") if column.mask_rule else None
                )
                col.row_filter = [rule.model_dump(mode="json") for rule in column.row_filters]
        await self.session.flush()

    async def expire(
        self, table_ids: list[int], column_ids: list[int], team_id: int | None = None
    ) -> None:
        table_query = update(TeamTableGrant).where(TeamTableGrant.table_id.in_(table_ids))
        column_query = update(TeamColumnGrant).where(TeamColumnGrant.column_id.in_(column_ids))
        if team_id is not None:
            table_query = table_query.where(TeamTableGrant.team_id == team_id)
            column_query = column_query.where(TeamColumnGrant.team_id == team_id)
        await self.session.execute(table_query.values(valid=False, member_visible=False))
        await self.session.execute(column_query.values(valid=False, member_visible=False))

    async def initialize_owner(
        self, team_id: int, tables: list[tuple[int, list[int]]], actor_id: int
    ) -> None:
        for table_id, column_ids in tables:
            table = await self.session.scalar(
                select(TeamTableGrant).where(
                    TeamTableGrant.team_id == team_id, TeamTableGrant.table_id == table_id
                )
            )
            if table is None:
                self.session.add(
                    TeamTableGrant(
                        team_id=team_id,
                        table_id=table_id,
                        member_visible=True,
                        valid=True,
                        created_by=actor_id,
                    )
                )
            for column_id in column_ids:
                column = await self.session.scalar(
                    select(TeamColumnGrant).where(
                        TeamColumnGrant.team_id == team_id, TeamColumnGrant.column_id == column_id
                    )
                )
                if column is None:
                    self.session.add(
                        TeamColumnGrant(
                            team_id=team_id,
                            column_id=column_id,
                            member_visible=True,
                            valid=True,
                            row_filter=[],
                            created_by=actor_id,
                        )
                    )
        await self.session.flush()

    async def set_members(
        self,
        team_id: int,
        table_ids: list[int],
        column_ids: list[int],
        member_tables: set[int],
        member_columns: set[int],
    ) -> None:
        tables, columns = await self.grants(team_id, table_ids, column_ids)
        for table in tables:
            table.member_visible = table.table_id in member_tables
        for column in columns:
            column.member_visible = column.column_id in member_columns
        await self.session.flush()
