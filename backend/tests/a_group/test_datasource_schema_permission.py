import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.datasource import ColumnMetadata, ConnectionTest, MetadataSnapshot, TableMetadata
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.masking import MaskKind, MaskRule
from app.core.sql.guard import SqlGuard
from app.modules.datasource.models import DatasourceRecord
from app.modules.datasource.schemas import DatasourceWrite
from app.modules.permission.models import TeamColumnGrant
from app.modules.permission.schemas import (
    ColumnGrantWrite,
    MemberGrantsWrite,
    MemberTableWrite,
    TableGrantWrite,
    TeamGrantsWrite,
)


def snapshot(*columns):
    return MetadataSnapshot(
        tables=[
            TableMetadata(
                schema_name="public",
                name="orders",
                comment="db comment",
                columns=[
                    ColumnMetadata(
                        name=name, data_type="TEXT", ordinal=index, comment="database comment"
                    )
                    for index, name in enumerate(columns, 1)
                ],
            )
        ]
    )


def draft():
    return DatasourceWrite(
        name="warehouse",
        type="postgresql",
        host="localhost",
        port=5432,
        database="warehouse",
        username="readonly",
        password=SecretStr("datasource-password"),
        tls=False,
    )


@pytest.fixture
async def catalog(identity_db):
    world = identity_db
    world.inspector.snapshot = snapshot("id", "phone", "secret")
    async with world.database.transaction() as session:
        source = await world.datasource(session).create(draft(), actor=world.admin)
        await world.schema(session).synchronize(source.id, world.grants(session), actor=world.admin)
        table = (await world.schema(session).catalog(source.id))[0]
    world.source, world.table = source.id, table
    world.columns = {c.name: c.id for c in table.columns}
    return world


async def authorize(world, session):
    return await world.grants(session).replace(
        world.source,
        world.team,
        TeamGrantsWrite(
            tables=[
                TableGrantWrite(
                    table_id=world.table.id,
                    columns=[
                        ColumnGrantWrite(column_id=world.columns["id"]),
                        ColumnGrantWrite(
                            column_id=world.columns["phone"],
                            mask_rule=MaskRule(kind=MaskKind.PHONE),
                        ),
                    ],
                )
            ]
        ),
        actor=world.admin,
    )


async def test_datasource_connection_warnings_secrets_and_roles(identity_db):
    world = identity_db
    world.inspector.result = ConnectionTest(
        available=True, read_only_account=False, warning="Account has write privileges"
    )
    async with world.database.transaction() as session:
        service = world.datasource(session)
        with pytest.raises(AppError):
            await service.create(draft(), actor=world.leader)
        created = await service.create(draft(), actor=world.admin)
        assert created.password == "********" and created.warning
        connection = await service.connection(created.id)
        assert not connection.read_only
        row = await session.get(DatasourceRecord, created.id)
        assert "datasource-password" not in row.secret_enc
        assert connection.password.get_secret_value() == "datasource-password"
        result = await service.test(created.id, actor=world.admin)
        assert not result.read_only_account
        audits = list(await session.scalars(select(AuditLog)))
        assert any(r.payload["diff"].get("warning") for r in audits)
        with pytest.raises(AppError) as error:
            await service.update(
                created.id, draft().model_copy(update={"host": "another"}), actor=world.admin
            )
        assert error.value.code == ErrorCode.CONFLICT
    world.inspector.result = ConnectionTest(available=False, read_only_account=False)
    async with world.database.transaction() as session:
        with pytest.raises(AppError) as error:
            await world.datasource(session).create(draft(), actor=world.admin)
        assert error.value.code == ErrorCode.DATASOURCE_UNAVAILABLE


async def test_default_denial_team_subset_masks_cache_and_no_metadata_leak(catalog):
    world = catalog
    async with world.database.transaction() as session:
        grants = world.grants(session)
        assert not (await grants.permissions.visible_scope(world.member, world.source)).tables
        assert not (await grants.permissions.visible_scope(world.leader, world.source)).tables
        assert (await grants.permissions.visible_scope(world.admin, world.source)).tables
        await authorize(world, session)
        assert world.cache.generation >= 2
        leader_scope = await grants.permissions.visible_scope(world.leader, world.source)
        assert leader_scope.tables["public.orders"].columns == {"id", "phone"}
        assert not (await grants.permissions.visible_scope(world.member, world.source)).tables
        await grants.set_members(
            world.source,
            world.team,
            MemberGrantsWrite(
                tables=[
                    MemberTableWrite(
                        table_id=world.table.id,
                        column_ids=[world.columns["id"], world.columns["phone"]],
                    )
                ]
            ),
            actor=world.leader,
        )
        member_scope = await grants.permissions.visible_scope(world.member, world.source)
        assert member_scope.tables["public.orders"].masks["phone"].kind == MaskKind.PHONE
        assert (
            member_scope.tables["public.orders"].columns
            <= leader_scope.tables["public.orders"].columns
        )
        public = (await grants.public_scope(world.source, actor=world.member)).model_dump_json()
        assert "secret" not in public and "all_columns" not in public
        browsed = await world.schema(session).browse(
            world.source, grants.permissions, actor=world.member
        )
        assert {c.name for c in browsed[0].columns} == {"id", "phone"}
        checked = SqlGuard().check("SELECT phone FROM public.orders", "postgres", member_scope)
        assert checked.output_masks["phone"].kind == MaskKind.PHONE
        with pytest.raises(AppError):
            SqlGuard().check("SELECT * FROM public.orders", "postgres", member_scope)


async def test_reauthorization_rejects_foreign_team_and_ungranted_columns(catalog):
    world = catalog
    async with world.database.transaction() as session:
        grants = world.grants(session)
        await authorize(world, session)
        with pytest.raises(AppError):
            await grants.replace(world.source, world.team, TeamGrantsWrite(), actor=world.leader)
        with pytest.raises(AppError):
            await grants.set_members(
                world.source, world.other, MemberGrantsWrite(), actor=world.leader
            )
        with pytest.raises(AppError):
            await grants.set_members(
                world.source,
                world.team,
                MemberGrantsWrite(
                    tables=[
                        MemberTableWrite(
                            table_id=world.table.id, column_ids=[world.columns["secret"]]
                        )
                    ]
                ),
                actor=world.leader,
            )
        with pytest.raises(AppError):
            await grants.set_members(
                world.source, world.team, MemberGrantsWrite(), actor=world.member
            )
        with pytest.raises(AppError):
            await grants.replace(
                world.source,
                world.team,
                TeamGrantsWrite(
                    tables=[
                        TableGrantWrite(
                            table_id=world.table.id, columns=[ColumnGrantWrite(column_id=999)]
                        )
                    ]
                ),
                actor=world.admin,
            )
        with pytest.raises(AppError):
            await grants.replace(
                world.source,
                world.team,
                TeamGrantsWrite(
                    tables=[
                        TableGrantWrite(
                            table_id=world.table.id,
                            columns=[
                                ColumnGrantWrite(column_id=world.columns["id"], member_visible=True)
                            ],
                        )
                    ]
                ),
                actor=world.admin,
            )
        outsider = CurrentUser(id=99, role=Role.LEADER, team_id=world.other)
        assert not (await grants.permissions.visible_scope(outsider, world.source)).tables


async def test_sync_preserves_descriptions_expires_missing_grants_and_never_auto_grants(catalog):
    world = catalog
    async with world.database.transaction() as session:
        await authorize(world, session)
        await world.schema(session).describe_table(world.table.id, "human table", actor=world.admin)
        await world.schema(session).describe_column(
            world.columns["id"], "human field", actor=world.admin
        )
    world.inspector.snapshot = snapshot("id", "new_field")
    async with world.database.transaction() as session:
        grants, schema = world.grants(session), world.schema(session)
        result = await schema.synchronize(world.source, grants, actor=world.admin)
        assert set(result.removed_columns) == {world.columns["phone"], world.columns["secret"]}
        table = (await schema.catalog(world.source))[0]
        assert table.description == "human table"
        assert table.columns[0].description == "human field"
        scope = await grants.permissions.visible_scope(world.leader, world.source)
        assert scope.tables["public.orders"].columns == {"id"}
        grant = await session.scalar(
            select(TeamColumnGrant).where(TeamColumnGrant.column_id == world.columns["phone"])
        )
        assert not grant.valid and not grant.member_visible
    world.inspector.snapshot = snapshot("id", "phone", "new_field")
    async with world.database.transaction() as session:
        grants = world.grants(session)
        await world.schema(session).synchronize(world.source, grants, actor=world.admin)
        assert (await grants.permissions.visible_scope(world.leader, world.source)).tables[
            "public.orders"
        ].columns == {"id"}
    world.inspector.snapshot = MetadataSnapshot(tables=[])
    async with world.database.transaction() as session:
        grants = world.grants(session)
        await world.schema(session).synchronize(world.source, grants, actor=world.admin)
        assert not (await grants.permissions.visible_scope(world.leader, world.source)).tables
