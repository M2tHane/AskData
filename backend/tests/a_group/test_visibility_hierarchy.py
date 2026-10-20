import pytest
from pydantic import SecretStr

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError
from app.modules.org.schemas import TeamCreate, TeamWrite, UserWrite
from app.modules.permission.schemas import DatasourceVisibilityWrite, ObjectVisibilityWrite
from app.modules.permission.visibility import ColumnNode, TableNode, resolve
from tests.a_group.test_datasource_schema_permission import draft, snapshot


@pytest.fixture
async def hierarchy(identity_db):
    world = identity_db
    world.inspector.snapshot = snapshot("id", "phone", "secret")
    async with world.database.transaction() as session:
        source = await world.datasource(session).create(draft(), actor=world.admin)
        await world.schema(session).synchronize(source.id, world.grants(session), actor=world.admin)
        table = (await world.schema(session).catalog(source.id))[0]
    world.source, world.table = source.id, table
    world.columns = {c.name: c.id for c in table.columns}
    return world


def test_resolve_inherits_and_clips_custom_objects():
    nodes = [
        TableNode(
            id=1,
            columns=[ColumnNode(id=10), ColumnNode(id=11, custom=True, teams={1, 2, 3})],
        ),
        TableNode(id=2, custom=True, teams={2, 9}, columns=[ColumnNode(id=20)]),
    ]
    resolved = resolve({1, 2}, nodes)
    assert resolved.tables == {1: {1, 2}, 2: {2}}
    assert resolved.columns == {10: {1, 2}, 11: {1, 2}, 20: {2}}


async def test_datasource_scope_flows_to_tables_and_columns_with_custom_overrides(hierarchy):
    world = hierarchy
    column = world.columns["secret"]
    async with world.database.transaction() as session:
        grants = world.grants(session)
        read = await grants.set_datasource_scope(
            world.source,
            DatasourceVisibilityWrite(team_ids=[world.team, world.other]),
            actor=world.admin,
        )
        assert read.team_ids == sorted([world.team, world.other]) and not read.custom
        assert all(
            c.team_ids == read.team_ids for t in read.tables for c in t.columns if not c.custom
        )
        other_leader = CurrentUser(id=1, role=Role.LEADER, team_id=world.other)
        scope = await grants.permissions.visible_scope(other_leader, world.source)
        assert scope.tables["public.orders"].columns == {"id", "phone", "secret"}
        # A single column is restricted to one team; the datasource becomes "custom".
        read = await grants.set_column_scope(
            world.source,
            column,
            ObjectVisibilityWrite(team_ids=[world.team]),
            actor=world.admin,
        )
        assert read.custom
        scope = await grants.permissions.visible_scope(other_leader, world.source)
        assert "secret" not in scope.tables["public.orders"].columns
        # A column cannot reach a team its table does not have.
        with pytest.raises(AppError):
            await grants.set_column_scope(
                world.source,
                column,
                ObjectVisibilityWrite(team_ids=[world.team, 9999]),
                actor=world.admin,
            )
        # Removing the team from the datasource revokes it everywhere, custom or not.
        await grants.set_datasource_scope(
            world.source, DatasourceVisibilityWrite(team_ids=[world.other]), actor=world.admin
        )
        assert not (await grants.permissions.visible_scope(world.leader, world.source)).tables
        # Re-adding the team does not resurrect the customized column.
        await grants.set_datasource_scope(
            world.source,
            DatasourceVisibilityWrite(team_ids=[world.team, world.other]),
            actor=world.admin,
        )
        scope = await grants.permissions.visible_scope(world.leader, world.source)
        assert "secret" not in scope.tables["public.orders"].columns
        reset = await grants.reset_visibility(world.source, actor=world.admin)
        assert not reset.custom
        scope = await grants.permissions.visible_scope(world.leader, world.source)
        assert "secret" in scope.tables["public.orders"].columns


async def test_only_admin_edits_visibility_and_table_scope_stays_inside_datasource(hierarchy):
    world = hierarchy
    async with world.database.transaction() as session:
        grants = world.grants(session)
        with pytest.raises(AppError):
            await grants.set_datasource_scope(
                world.source, DatasourceVisibilityWrite(team_ids=[world.team]), actor=world.leader
            )
        await grants.set_datasource_scope(
            world.source, DatasourceVisibilityWrite(team_ids=[world.team]), actor=world.admin
        )
        with pytest.raises(AppError):
            await grants.set_table_scope(
                world.source,
                world.table.id,
                ObjectVisibilityWrite(team_ids=[world.other]),
                actor=world.admin,
            )
        read = await grants.set_table_scope(
            world.source, world.table.id, ObjectVisibilityWrite(team_ids=[]), actor=world.admin
        )
        assert read.tables[0].custom and read.tables[0].team_ids == []
        assert not (await grants.permissions.visible_scope(world.leader, world.source)).tables
        back = await grants.set_table_scope(
            world.source, world.table.id, ObjectVisibilityWrite(inherit=True), actor=world.admin
        )
        assert not back.custom and back.tables[0].team_ids == [world.team]


async def test_free_employees_team_membership_and_leader_changes(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        org = world.org(session)

        async def employee(name: str, *, team_id: int | None = None):
            return await org.create_user(
                UserWrite(
                    username=name,
                    password=SecretStr("free-employee-password"),
                    role=Role.MEMBER,
                    team_id=team_id,
                ),
                actor=world.admin,
            )

        free_a, free_b = await employee("free-a"), await employee("free-b")
        listed = await org.list_users(_page(), actor=world.admin, free=True)
        assert {u.username for u in listed.items} == {"free-a", "free-b"}
        # A free employee cannot sign in until assigned.
        with pytest.raises(AppError):
            await org.identity(free_a.id)
        team = await org.create_team(TeamCreate(name="ops", leader_id=free_a.id), actor=world.admin)
        assert team.leader_id == free_a.id and team.member_count == 1
        team = await org.add_members(team.id, [free_b.id], actor=world.admin)
        assert team.member_count == 2
        # Members of another team are not in the free pool.
        with pytest.raises(AppError):
            await org.add_members(world.other, [world.member.id], actor=world.admin)
        # The new leader may come from the team; the old one becomes a free employee.
        team = await org.change_leader(team.id, free_b.id, actor=world.admin)
        assert team.leader_id == free_b.id
        old = await org.user(free_a.id)
        assert old.role == Role.MEMBER and old.team_id is None
        # A member of a different team cannot be made leader.
        with pytest.raises(AppError):
            await org.change_leader(team.id, world.member.id, actor=world.admin)
        # The leader cannot be removed; use change_leader first.
        with pytest.raises(AppError):
            await org.remove_member(team.id, free_b.id, actor=world.admin)
        team = await org.change_leader(team.id, free_a.id, actor=world.admin)
        assert (await org.user(free_b.id)).team_id is None and team.member_count == 1
        assert (await org.add_members(team.id, [free_b.id], actor=world.admin)).member_count == 2
        team = await org.remove_member(team.id, free_b.id, actor=world.admin)
        assert (await org.user(free_b.id)).team_id is None and team.member_count == 1
        with pytest.raises(AppError):
            await org.change_leader(team.id, free_a.id, actor=world.leader)
        listed_teams = await org.list_teams(_page(), actor=world.admin)
        assert all(t.member_count >= 0 for t in listed_teams.items)
        assert TeamWrite(name="x").status == "active"


def _page():
    from app.core.response import PageParams

    return PageParams(page=1, page_size=50)


async def test_full_visibility_save_is_atomic_and_unlisted_objects_inherit(hierarchy):
    from app.modules.permission.schemas import (
        ColumnScopeWrite,
        FullVisibilityWrite,
        TableScopeWrite,
    )

    world = hierarchy
    secret = world.columns["secret"]
    async with world.database.transaction() as session:
        grants = world.grants(session)
        read = await grants.set_visibility_full(
            world.source,
            FullVisibilityWrite(
                team_ids=[world.team, world.other],
                tables=[
                    TableScopeWrite(
                        table_id=world.table.id,
                        columns=[ColumnScopeWrite(column_id=secret, team_ids=[world.other])],
                    )
                ],
            ),
            actor=world.admin,
        )
        assert read.custom and not read.tables[0].custom
        by_column = {c.column_id: c for c in read.tables[0].columns}
        assert by_column[secret].team_ids == [world.other] and by_column[secret].custom
        scope = await grants.permissions.visible_scope(world.leader, world.source)
        assert scope.tables["public.orders"].columns == {"id", "phone"}
    async with world.database.transaction() as session:
        grants = world.grants(session)
        # An invalid column set rejects the whole save and leaves the earlier state intact.
        with pytest.raises(AppError):
            await grants.set_visibility_full(
                world.source,
                FullVisibilityWrite(
                    team_ids=[world.team],
                    tables=[
                        TableScopeWrite(
                            table_id=world.table.id,
                            columns=[ColumnScopeWrite(column_id=secret, team_ids=[world.other])],
                        )
                    ],
                ),
                actor=world.admin,
            )
        cleared = await grants.set_visibility_full(
            world.source, FullVisibilityWrite(team_ids=[world.team]), actor=world.admin
        )
        assert not cleared.custom and cleared.team_ids == [world.team]


async def test_people_lists_whom_the_caller_may_review(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        org = world.org(session)
        everyone = {p.id for p in await org.people(actor=world.admin)}
        team = {p.id for p in await org.people(actor=world.leader)}
        assert {world.leader.id, world.member.id} <= team < everyone
        with pytest.raises(AppError):
            await org.people(actor=world.member)
