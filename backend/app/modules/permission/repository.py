from sqlalchemy import select, update

from app.core.db import AsyncSession
from app.modules.permission.models import (
    DatasourceTeamScope,
    ScopeOverride,
    TeamColumnGrant,
    TeamTableGrant,
)
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

    async def datasource_teams(self, datasource_id: int) -> set[int]:
        rows = await self.session.scalars(
            select(DatasourceTeamScope.team_id).where(
                DatasourceTeamScope.datasource_id == datasource_id
            )
        )
        return set(rows)

    async def set_datasource_teams(
        self, datasource_id: int, team_ids: set[int], actor_id: int
    ) -> None:
        rows = list(
            await self.session.scalars(
                select(DatasourceTeamScope).where(
                    DatasourceTeamScope.datasource_id == datasource_id
                )
            )
        )
        for row in rows:
            if row.team_id not in team_ids:
                await self.session.delete(row)
        present = {row.team_id for row in rows}
        for team_id in sorted(team_ids - present):
            self.session.add(
                DatasourceTeamScope(
                    datasource_id=datasource_id, team_id=team_id, created_by=actor_id
                )
            )
        await self.session.flush()

    async def overrides(
        self, table_ids: list[int], column_ids: list[int]
    ) -> tuple[set[int], set[int]]:
        rows = await self.session.execute(
            select(ScopeOverride.object_type, ScopeOverride.object_id).where(
                ((ScopeOverride.object_type == "table") & ScopeOverride.object_id.in_(table_ids))
                | (
                    (ScopeOverride.object_type == "column")
                    & ScopeOverride.object_id.in_(column_ids)
                )
            )
        )
        found = list(rows)
        return (
            {i for kind, i in found if kind == "table"},
            {i for kind, i in found if kind == "column"},
        )

    async def store_overrides(
        self,
        table_ids: list[int],
        column_ids: list[int],
        custom_tables: set[int],
        custom_columns: set[int],
        actor_id: int,
    ) -> None:
        current_tables, current_columns = await self.overrides(table_ids, column_ids)
        for kind, scope, wanted, current in (
            ("table", table_ids, custom_tables, current_tables),
            ("column", column_ids, custom_columns, current_columns),
        ):
            gone = [i for i in current if i in scope and i not in wanted]
            if gone:
                rows = await self.session.scalars(
                    select(ScopeOverride).where(
                        ScopeOverride.object_type == kind, ScopeOverride.object_id.in_(gone)
                    )
                )
                for row in rows:
                    await self.session.delete(row)
            for identifier in sorted((wanted & set(scope)) - current):
                self.session.add(
                    ScopeOverride(object_type=kind, object_id=identifier, created_by=actor_id)
                )
        await self.session.flush()

    async def team_sets(
        self, table_ids: list[int], column_ids: list[int]
    ) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
        tables: dict[int, set[int]] = {}
        columns: dict[int, set[int]] = {}
        for table_id, team_id in await self.session.execute(
            select(TeamTableGrant.table_id, TeamTableGrant.team_id).where(
                TeamTableGrant.table_id.in_(table_ids), TeamTableGrant.valid.is_(True)
            )
        ):
            tables.setdefault(table_id, set()).add(team_id)
        for column_id, team_id in await self.session.execute(
            select(TeamColumnGrant.column_id, TeamColumnGrant.team_id).where(
                TeamColumnGrant.column_id.in_(column_ids), TeamColumnGrant.valid.is_(True)
            )
        ):
            columns.setdefault(column_id, set()).add(team_id)
        return tables, columns

    async def apply_sets(
        self,
        table_sets: dict[int, set[int]],
        column_sets: dict[int, set[int]],
        actor_id: int,
    ) -> int:
        """Make valid grants equal the given team sets; new rows default to employee-visible.

        Returns the number of grant rows newly made valid. Existing mask rules and row
        filters stay on rows that are revoked and later re-granted.
        """
        created = 0
        table_rows = {
            (row.table_id, row.team_id): row
            for row in await self.session.scalars(
                select(TeamTableGrant).where(TeamTableGrant.table_id.in_(list(table_sets)))
            )
        }
        column_rows = {
            (row.column_id, row.team_id): row
            for row in await self.session.scalars(
                select(TeamColumnGrant).where(TeamColumnGrant.column_id.in_(list(column_sets)))
            )
        }
        for identifier, wanted in table_sets.items():
            for team_id in wanted:
                table_row = table_rows.get((identifier, team_id))
                if table_row is None:
                    self.session.add(
                        TeamTableGrant(
                            team_id=team_id,
                            table_id=identifier,
                            member_visible=True,
                            valid=True,
                            created_by=actor_id,
                        )
                    )
                    created += 1
                elif not table_row.valid:
                    table_row.valid, table_row.member_visible = True, True
                    created += 1
        for identifier, wanted in column_sets.items():
            for team_id in wanted:
                column_row = column_rows.get((identifier, team_id))
                if column_row is None:
                    self.session.add(
                        TeamColumnGrant(
                            team_id=team_id,
                            column_id=identifier,
                            member_visible=True,
                            valid=True,
                            created_by=actor_id,
                        )
                    )
                    created += 1
                elif not column_row.valid:
                    column_row.valid, column_row.member_visible = True, True
                    created += 1
        for (identifier, team_id), table_row in table_rows.items():
            if table_row.valid and team_id not in table_sets.get(identifier, set()):
                table_row.valid, table_row.member_visible = False, False
        for (identifier, team_id), column_row in column_rows.items():
            if column_row.valid and team_id not in column_sets.get(identifier, set()):
                column_row.valid, column_row.member_visible = False, False
        await self.session.flush()
        return created
