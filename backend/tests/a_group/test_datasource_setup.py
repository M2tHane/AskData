"""Datasource setup with required semantics: tables are imported by choice, every column of an
imported table has a semantic, and nothing waiting reaches anyone (catalog, scope, the model)."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.core.datasource import ColumnMetadata, MetadataSnapshot, TableMetadata
from app.core.errors import AppError, ErrorCode
from app.modules.permission.schemas import ColumnGrantWrite, TableGrantWrite, TeamGrantsWrite
from app.modules.schema.models import MetaColumn, MetaTable
from app.modules.schema.schemas import (
    DraftRequest,
    SetupColumnWrite,
    SetupTableWrite,
    SetupWrite,
)
from tests.a_group.test_datasource_schema_permission import draft

ROOT = Path(__file__).resolve().parents[2]


def table(name, comment="", **columns):
    """columns: name -> comment ("" for none)."""
    return TableMetadata(
        schema_name="public",
        name=name,
        comment=comment,
        columns=[
            ColumnMetadata(name=column, data_type="TEXT", ordinal=index, comment=note)
            for index, (column, note) in enumerate(columns.items(), 1)
        ],
    )


SHOP = [
    table("orders", "订单", id="订单号", amount="实付金额", memo=""),
    table("customers", "客户", id="客户号", phone=""),
    table("audit_trail", "", id="", payload=""),
]


async def sync(world, source_id):
    async with world.database.transaction() as session:
        return await world.schema(session).synchronize(
            source_id, world.grants(session), actor=world.admin
        )


async def create(world, strict=True, tables=SHOP):
    world.inspector.snapshot = MetadataSnapshot(tables=list(tables))
    async with world.database.transaction() as session:
        source = await world.datasource(session).create(
            draft().model_copy(update={"require_semantics": strict}), actor=world.admin
        )
    await sync(world, source.id)
    return source.id


async def setup(world, source_id):
    async with world.database.transaction() as session:
        read = await world.schema(session).setup(source_id, actor=world.admin)
    return {t.name: t for t in read.tables}


async def save(world, source_id, tables, publish):
    async with world.database.transaction() as session:
        return await world.schema(session).save_setup(
            source_id,
            SetupWrite(tables=tables, publish=publish),
            world.grants(session),
            actor=world.admin,
        )


async def catalog(world, source_id):
    async with world.database.transaction() as session:
        return {
            t.name: {c.name for c in t.columns}
            for t in await world.schema(session).catalog(source_id)
        }


def write(table, include=True, **descriptions):
    return SetupTableWrite(
        table_id=table.id,
        include=include,
        columns=[
            SetupColumnWrite(column_id=c.id, description=descriptions.get(c.name, c.comment))
            for c in table.columns
        ],
    )


async def test_a_new_strict_datasource_shows_nothing_until_set_up(identity_db):
    world = identity_db
    source = await create(world)
    assert await catalog(world, source) == {}
    tables = await setup(world, source)
    assert {name: t.status for name, t in tables.items()} == {
        "orders": "pending",
        "customers": "pending",
        "audit_trail": "pending",
    }
    # Commented columns already have their semantic; the others wait for one.
    assert {c.name: c.status for c in tables["orders"].columns} == {
        "id": "active",
        "amount": "active",
        "memo": "pending",
    }
    async with world.database.transaction() as session:
        grants = world.grants(session)
        assert not (await grants.permissions.visible_scope(world.admin, source)).tables


async def test_publishing_requires_a_semantic_for_every_selected_column(identity_db):
    world = identity_db
    source = await create(world)
    tables = await setup(world, source)
    with pytest.raises(AppError) as refused:
        await save(
            world,
            source,
            [write(tables["orders"]), write(tables["customers"]), write(tables["audit_trail"])],
            publish=True,
        )
    # memo, phone, and the two audit_trail columns.
    assert refused.value.code == ErrorCode.VALIDATION_ERROR and "4 个字段" in refused.value.message
    assert await catalog(world, source) == {}
    # Leaving audit_trail out and describing the rest is enough.
    result = await save(
        world,
        source,
        [
            write(tables["orders"], memo="订单备注"),
            write(tables["customers"], phone="客户手机号"),
            write(tables["audit_trail"], include=False),
        ],
        publish=True,
    )
    assert (result.imported_tables, result.released_columns) == (2, 2)
    assert await catalog(world, source) == {
        "orders": {"id", "amount", "memo"},
        "customers": {"id", "phone"},
    }
    after = await setup(world, source)
    assert after["audit_trail"].status == "excluded"
    columns = {c.name: c for c in after["orders"].columns}
    # A semantic equal to the comment is stored as "use the comment".
    assert columns["amount"].description == "" and columns["memo"].description == "订单备注"


async def test_a_draft_keeps_work_and_changes_nothing_visible(identity_db):
    world = identity_db
    source = await create(world)
    tables = await setup(world, source)
    result = await save(
        world,
        source,
        [write(tables["orders"], memo="订单备注"), write(tables["audit_trail"], include=False)],
        publish=False,
    )
    assert not result.published and await catalog(world, source) == {}
    again = await setup(world, source)
    assert {c.name: c.description for c in again["orders"].columns}["memo"] == "订单备注"
    assert again["orders"].status == "pending" and again["audit_trail"].status == "excluded"
    # Coming back: customers still needs phone, orders is complete.
    with pytest.raises(AppError):
        await save(world, source, [], publish=True)
    await save(world, source, [write(again["customers"], phone="手机号")], publish=True)
    assert set(await catalog(world, source)) == {"orders", "customers"}


async def test_resync_holds_back_new_tables_and_undescribed_columns(identity_db):
    world = identity_db
    source = await create(world)
    tables = await setup(world, source)
    await save(
        world,
        source,
        [
            write(tables["orders"], memo="订单备注"),
            write(tables["customers"], phone="手机号"),
            write(tables["audit_trail"], include=False),
        ],
        publish=True,
    )
    world.inspector.snapshot = MetadataSnapshot(
        tables=[
            table(
                "orders",
                "订单",
                id="订单号",
                amount="实付金额",
                memo="",
                channel="",
                coupon="优惠券",
            ),
            table("customers", "客户", id="客户号", phone=""),
            # audit_trail is gone from the database for now; refunds is new.
            table("refunds", "退款", id="退款号"),
        ]
    )
    result = await sync(world, source)
    assert len(result.pending_tables) == 1 and len(result.pending_columns) == 1
    # coupon has a comment: it shows at once. channel waits; refunds waits to be imported.
    assert await catalog(world, source) == {
        "orders": {"id", "amount", "memo", "coupon"},
        "customers": {"id", "phone"},
    }
    tables = await setup(world, source)
    assert tables["audit_trail"].status == "excluded"
    assert tables["refunds"].status == "pending"
    world.inspector.snapshot = MetadataSnapshot(
        tables=[*SHOP, table("refunds", "退款", id="退款号")]
    )
    await sync(world, source)
    # Back in the database, still left out.
    assert (await setup(world, source))["audit_trail"].status == "excluded"


async def test_a_column_whose_comment_appears_is_released_by_the_next_sync(identity_db):
    world = identity_db
    source = await create(world, tables=[table("orders", "订单", id="订单号", memo="")])
    tables = await setup(world, source)
    await save(world, source, [write(tables["orders"], memo="备注")], publish=True)
    world.inspector.snapshot = MetadataSnapshot(
        tables=[table("orders", "订单", id="订单号", memo="", note="")]
    )
    await sync(world, source)
    assert "note" not in (await catalog(world, source))["orders"]
    world.inspector.snapshot = MetadataSnapshot(
        tables=[table("orders", "订单", id="订单号", memo="", note="内部备注")]
    )
    await sync(world, source)
    assert "note" in (await catalog(world, source))["orders"]


async def test_older_datasources_sync_as_before_and_are_only_reminded(identity_db):
    world = identity_db
    source = await create(world, strict=False)
    assert set(await catalog(world, source)) == {"orders", "customers", "audit_trail"}
    async with world.database.transaction() as session:
        summaries = {
            s.datasource_id: s for s in await world.schema(session).summaries(actor=world.admin)
        }
    summary = summaries[source]
    assert not summary.require_semantics
    assert (summary.pending_tables, summary.pending_columns) == (0, 0)
    assert summary.undocumented_columns == 4  # memo, phone, audit_trail.id, audit_trail.payload
    # Uploaded files have headers, not comments: they are never flagged.
    async with world.database.transaction() as session:
        await session.execute(
            text("UPDATE datasource SET type = 'upload' WHERE id = :id"), {"id": source}
        )
        summaries = await world.schema(session).summaries(actor=world.admin)
    assert source not in {s.datasource_id for s in summaries}


async def test_summary_counts_what_a_strict_datasource_still_needs(identity_db):
    world = identity_db
    source = await create(world)
    async with world.database.transaction() as session:
        summary = next(
            s
            for s in await world.schema(session).summaries(actor=world.admin)
            if s.datasource_id == source
        )
    assert (summary.tables, summary.pending_tables, summary.pending_columns) == (0, 3, 0)
    with pytest.raises(AppError):
        async with world.database.transaction() as session:
            await world.schema(session).summaries(actor=world.leader)


async def test_imported_tables_inherit_team_visibility_and_dropped_ones_lose_it(identity_db):
    world = identity_db
    source = await create(world, tables=[table("orders", "订单", id="订单号")])
    tables = await setup(world, source)
    await save(world, source, [write(tables["orders"])], publish=True)
    orders = (await setup(world, source))["orders"]
    async with world.database.transaction() as session:
        await world.grants(session).replace(
            source,
            world.team,
            TeamGrantsWrite(
                tables=[
                    TableGrantWrite(
                        table_id=orders.id,
                        columns=[ColumnGrantWrite(column_id=c.id) for c in orders.columns],
                    )
                ]
            ),
            actor=world.admin,
        )
        scope = await world.grants(session).permissions.visible_scope(world.leader, source)
        assert set(scope.tables) == {"public.orders"}
    # Dropping an imported table takes it away from everyone at once.
    await save(world, source, [write(orders, include=False)], publish=True)
    async with world.database.transaction() as session:
        scope = await world.grants(session).permissions.visible_scope(world.leader, source)
    assert not scope.tables
    assert (await setup(world, source))["orders"].status == "excluded"


async def test_only_admins_set_up_and_ids_must_belong_to_the_datasource(identity_db):
    world = identity_db
    source = await create(world)
    other = await create(world, tables=[table("elsewhere", "别处", id="编号")])
    elsewhere = (await setup(world, other))["elsewhere"]
    with pytest.raises(AppError):
        async with world.database.transaction() as session:
            await world.schema(session).setup(source, actor=world.leader)
    with pytest.raises(AppError) as foreign:
        await save(world, source, [write(elsewhere)], publish=False)
    assert foreign.value.code == ErrorCode.NOT_FOUND


class Drafter:
    def __init__(self):
        self.calls = []

    async def draft(self, table, columns, *, actor):
        self.calls.append((table.name, [c.name for c in columns]))
        return {c.id: f"  {c.name} 的含义  " for c in columns} | {999999: "不该出现"}


async def test_drafts_propose_semantics_for_the_asked_columns_only(identity_db):
    world = identity_db
    source = await create(world)
    tables = await setup(world, source)
    memo = next(c for c in tables["orders"].columns if c.name == "memo")
    phone = next(c for c in tables["customers"].columns if c.name == "phone")
    drafter = Drafter()
    async with world.database.transaction() as session:
        service = world.schema(session)
        with pytest.raises(AppError) as unavailable:
            await service.draft(source, DraftRequest(column_ids=[memo.id]), actor=world.admin)
        assert unavailable.value.code == ErrorCode.LLM_UNAVAILABLE
        service.drafter = drafter
        result = await service.draft(
            source, DraftRequest(column_ids=[memo.id, phone.id]), actor=world.admin
        )
        assert result.descriptions == {memo.id: "memo 的含义", phone.id: "phone 的含义"}
        with pytest.raises(AppError):
            await service.draft(source, DraftRequest(column_ids=[999999]), actor=world.admin)
    # One call per table, with only that table's asked columns.
    assert sorted(drafter.calls) == [("customers", ["phone"]), ("orders", ["memo"])]
    # Proposals are not saved.
    assert {c.name: c.description for c in (await setup(world, source))["orders"].columns}[
        "memo"
    ] == ""


async def test_downgrading_0015_retires_everything_waiting(identity_db):
    world = identity_db
    source = await create(world)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    await asyncio.to_thread(command.downgrade, config, "0014")
    async with world.database.engine.connect() as connection:
        statuses = set(
            (
                await connection.execute(
                    text("SELECT status FROM meta_table WHERE datasource_id = :id"), {"id": source}
                )
            ).scalars()
        ) | set((await connection.execute(text("SELECT status FROM meta_column"))).scalars())
    assert statuses <= {"active", "missing"}
    await asyncio.to_thread(command.upgrade, config, "head")
    async with world.database.transaction() as session:
        assert await session.scalar(select(MetaTable.id).limit(1))
        assert await session.scalar(select(MetaColumn.id).limit(1))


class Transport:
    """Answers each drafting call with a meaning for every asked column, plus a made-up one."""

    def __init__(self, broken=False):
        self.prompts, self.broken = [], broken

    async def chat(self, model, messages):
        import json
        import re

        from app.core.llm import ChatResponse, Usage

        content = messages[0].content
        self.prompts.append(content)
        if self.broken:
            return ChatResponse(text="我觉得这些字段是……", usage=Usage())
        asked = json.loads(re.search(r'"describe": (\[[^\]]*\])', content).group(1))
        answer = {name: f"{name}含义" for name in asked} | {"invented": "编的"}
        return ChatResponse(text=json.dumps({"columns": answer}), usage=Usage(tokens_in=5))


async def test_the_model_drafter_batches_wide_tables_and_drops_invented_names():
    from pydantic import SecretStr

    from app.core.deps import CurrentUser, Role
    from app.core.llm import LLMClient, ModelConfig, ModelRegistry
    from app.modules.schema.drafting import BATCH, ModelDescriptionDrafter
    from app.modules.schema.schemas import ColumnRead, TableRead
    from tests.fakes import FakeModels, FakeUsage

    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    columns = [
        ColumnRead(
            id=n,
            table_id=1,
            name=f"c{n}",
            data_type="text",
            comment="",
            description="已知" if n == 1 else "",
            is_pk=False,
            ordinal=n,
            status="pending",
        )
        for n in range(1, BATCH + 12)
    ]
    wide = TableRead(
        id=1,
        datasource_id=1,
        schema_name="public",
        name="wide",
        comment="宽表",
        description="",
        status="pending",
        synced_at=now,
        columns=columns,
    )
    models = FakeModels([ModelConfig(id="m", kind="llm", model="fake", api_key=SecretStr("k"))])
    transport = Transport()
    drafter = ModelDescriptionDrafter(LLMClient(ModelRegistry(models), FakeUsage(), transport))
    admin = CurrentUser(id=1, role=Role.ADMIN)
    asked = columns[1:]
    found = await drafter.draft(wide, asked, actor=admin)
    assert len(transport.prompts) == 2
    assert found == {c.id: f"{c.name}含义" for c in asked}
    # The model sees names, types and known meanings, never data.
    assert '"meaning": "已知"' in transport.prompts[0]
    broken = ModelDescriptionDrafter(
        LLMClient(ModelRegistry(models), FakeUsage(), Transport(broken=True))
    )
    with pytest.raises(AppError) as unavailable:
        await broken.draft(wide, asked[:3], actor=admin)
    assert unavailable.value.code == ErrorCode.LLM_UNAVAILABLE
