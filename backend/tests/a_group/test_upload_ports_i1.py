import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.datasource import ColumnMetadata, Datasource, TableMetadata
from app.core.errors import AppError, ErrorCode
from app.core.sql.dialect import Dialect
from app.modules.datasource.models import DatasourceRecord
from app.modules.embedding.models import EmbeddingItem


def reader(identifier):
    return Datasource(
        id=identifier,
        dialect=Dialect.POSTGRES,
        host="pg-import",
        port=5432,
        database="imports",
        username=f"ds_{identifier}_reader",
        password=SecretStr("import-reader-secret"),
        schema_name=f"ds_{identifier}",
        tls=False,
    )


def table(identifier, name="orders", columns=("id", "phone")):
    return TableMetadata(
        schema_name=f"ds_{identifier}",
        name=name,
        columns=[
            ColumnMetadata(name=name, data_type="TEXT", ordinal=index)
            for index, name in enumerate(columns, 1)
        ],
    )


async def publish(world, session, identifier, job_id, incoming):
    source, schema, grants = world.datasource(session), world.schema(session), world.grants(session)
    receipt = await source.publish_upload_credentials(
        job_id, reader(identifier), world.team, actor=world.leader
    )
    if receipt.completed:
        return receipt
    result = await schema.synchronize_upload(identifier, incoming, actor=world.leader)
    await grants.expire_metadata(result)
    if receipt.initialize_owner_grants:
        await grants.grant_upload_owner(identifier, world.team, actor=world.leader)
    await source.finish_upload_publication(job_id, identifier, actor=world.leader)
    return receipt


async def test_upload_public_services_first_owner_grants_receipts_secrets_and_new_columns(
    identity_db,
):
    world = identity_db
    async with world.database.transaction() as session:
        source = world.datasource(session)
        with pytest.raises(AppError):
            await source.reserve_upload("member upload", world.team, actor=world.member)
        with pytest.raises(AppError):
            await source.reserve_upload("foreign upload", world.other, actor=world.leader)
        identifier = await source.reserve_upload("team upload", world.team, actor=world.leader)
        with pytest.raises(AppError) as caught:
            await source.connection(identifier)
        assert caught.value.code == ErrorCode.DATASOURCE_UNAVAILABLE
    async with world.database.transaction() as session:
        receipt = await publish(world, session, identifier, 1, table(identifier))
        assert receipt.initialize_owner_grants
        scope = await world.grants(session).visible_scope(world.member, identifier)
        assert scope.tables[f"ds_{identifier}.orders"].columns == {"id", "phone"}
        row = await session.get(DatasourceRecord, identifier)
        assert "import-reader-secret" not in row.secret_enc
        assert row.upload_initialized and row.upload_publications == {"1": "completed"}
        assert (
            await world.datasource(session).connection(identifier)
        ).password.get_secret_value() == "import-reader-secret"
    async with world.database.transaction() as session:
        assert (await publish(world, session, identifier, 1, table(identifier))).completed
        receipt = await publish(
            world, session, identifier, 2, table(identifier, columns=("id", "new_field"))
        )
        assert not receipt.initialize_owner_grants
        scope = await world.grants(session).visible_scope(world.member, identifier)
        assert scope.tables[f"ds_{identifier}.orders"].columns == {"id"}
        with pytest.raises(AppError):
            await world.grants(session).grant_upload_owner(
                identifier, world.team, actor=world.leader
            )
        row = await session.get(DatasourceRecord, identifier)
        assert row.upload_publications == {"1": "completed", "2": "completed"}
        assert list(
            await session.scalars(
                select(EmbeddingItem).where(EmbeddingItem.datasource_id == identifier)
            )
        )
        audits = list(await session.scalars(select(AuditLog)))
        assert {
            "datasource.upload.reserve",
            "datasource.upload.publish",
            "schema.upload.sync",
            "permission.upload.owner",
            "datasource.upload.finish",
        } <= {row.payload["action"] for row in audits}


async def test_upload_partial_sync_preserves_other_sheets_and_descriptions(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        identifier = await world.datasource(session).reserve_upload(
            "sheets", world.team, actor=world.leader
        )
        await publish(world, session, identifier, 1, table(identifier))
        metadata = (await world.schema(session).catalog(identifier))[0]
        await world.schema(session).describe_column(
            metadata.columns[0].id, "manual description", actor=world.admin
        )
        await publish(
            world, session, identifier, 2, table(identifier, name="customers", columns=("id",))
        )
        await publish(world, session, identifier, 3, table(identifier, columns=("id", "amount")))
        catalog = await world.schema(session).catalog(identifier)
        assert {item.name for item in catalog} == {"orders", "customers"}
        assert (
            next(item for item in catalog if item.name == "orders").columns[0].description
            == "manual description"
        )
        scope = await world.grants(session).visible_scope(world.member, identifier)
        assert set(scope.tables) == {f"ds_{identifier}.orders"}
        with pytest.raises(AppError):
            await world.datasource(session).publish_upload_credentials(
                4,
                reader(identifier).model_copy(update={"host": "other"}),
                world.team,
                actor=world.leader,
            )


async def test_upload_publication_rolls_back_receipt_grants_and_metadata_together(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        identifier = await world.datasource(session).reserve_upload(
            "rollback", world.team, actor=world.leader
        )
    with pytest.raises(AppError):
        async with world.database.transaction() as session:
            await publish(world, session, identifier, 1, table(identifier))
            raise AppError(ErrorCode.CONFLICT)
    async with world.database.transaction() as session:
        row = await session.get(DatasourceRecord, identifier)
        assert (
            row.status == "pending" and not row.upload_initialized and row.upload_publications == {}
        )
        assert await world.schema(session).catalog(identifier) == []
        assert (
            await publish(world, session, identifier, 1, table(identifier))
        ).initialize_owner_grants


async def test_admin_upload_has_no_owner_team_and_later_sheets_inherit_the_datasource_scope(
    identity_db,
):
    from app.modules.permission.schemas import DatasourceVisibilityWrite
    from app.modules.upload.adapters import build_upload_registration

    world = identity_db
    async with world.database.transaction() as session:
        grants = world.grants(session)
        registration = build_upload_registration(
            world.datasource(session),
            world.schema(session),
            grants,
            world.org(session),
            grants.permissions,
        )
        with pytest.raises(AppError):
            await registration.reserve("leader without team", None, world.leader)
        identifier = await registration.reserve("workbook", None, world.admin)
        assert await registration.owner_team(identifier) is None
        await registration.publish(
            1, reader(identifier), table(identifier, "sheet_a"), None, world.admin
        )
        # Nobody sees the data until the administrator chooses teams on the datasource.
        assert not (await grants.visible_scope(world.member, identifier)).tables
        await grants.set_datasource_scope(
            identifier, DatasourceVisibilityWrite(team_ids=[world.team]), actor=world.admin
        )
        scope = await grants.visible_scope(world.member, identifier)
        assert set(scope.tables) == {f"ds_{identifier}.sheet_a"}
        # A second sheet imported later inherits the same teams.
        await registration.publish(
            2, reader(identifier), table(identifier, "sheet_b"), None, world.admin
        )
        scope = await grants.visible_scope(world.member, identifier)
        assert set(scope.tables) == {f"ds_{identifier}.sheet_a", f"ds_{identifier}.sheet_b"}
        with pytest.raises(AppError):
            await registration.publish(
                3, reader(identifier), table(identifier, "x"), None, world.leader
            )
