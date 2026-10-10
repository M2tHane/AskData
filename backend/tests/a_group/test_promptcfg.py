import pytest

from app.core.errors import AppError
from app.core.prompt import PromptId, PromptRegistry
from app.modules.promptcfg.schemas import RevisionWrite
from app.modules.promptcfg.service import build_service

SQL_OK = "Write SQL for $engine.\n$dialect\n<untrusted_data>$data</untrusted_data>"


async def test_publish_history_rollback_and_runtime_load(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        service = build_service(session)
        before = await service.detail("system", "sql_generation", actor=world.admin)
        assert before.active_label.endswith("（内置）") and before.revisions == []

        first = await service.create(
            "system",
            "sql_generation",
            RevisionWrite(content=SQL_OK, note="第一版"),
            actor=world.admin,
        )
        second = await service.create(
            "system",
            "sql_generation",
            RevisionWrite(content=SQL_OK + "\nBe terse.", note="更简洁"),
            actor=world.admin,
        )
        assert (first.revision, second.revision) == (1, 2)
        detail = await service.detail("system", "sql_generation", actor=world.admin)
        assert [r.is_active for r in detail.revisions] == [True, False]
        assert detail.active_label == "r2" and detail.revisions[0].author

        overrides = await service.load()
        rendered = PromptRegistry().render(
            PromptId.SQL, {"q": "x"}, dialect_hint="HINT", engine="MySQL", overrides=overrides
        )
        assert rendered.version == "r2" and "Be terse." in rendered.content
        assert "Write SQL for MySQL." in rendered.content

        await service.activate("system", "sql_generation", first.id, actor=world.admin)
        assert (await service.load()).system["sql_generation"].label == "r1"
        await service.reset("system", "sql_generation", actor=world.admin)
        assert (await service.load()).system == {}
        assert (await service.detail("system", "sql_generation", actor=world.admin)).revisions


@pytest.mark.parametrize(
    "content",
    [
        "no data block $data $engine $dialect",
        "<untrusted_data>$data</untrusted_data> $engine",  # dialect variable missing
        "<untrusted_data>$data</untrusted_data> $engine $dialect $extra",
        "<untrusted_data>$data</untrusted_data> $engine $dialect cost $5",
        "",
    ],
)
async def test_invalid_templates_are_rejected(identity_db, content):
    world = identity_db
    async with world.database.transaction() as session:
        service = build_service(session)
        with pytest.raises(AppError):
            await service.create(
                "system", "sql_generation", RevisionWrite(content=content or " "), actor=world.admin
            )


async def test_dialect_override_and_admin_only(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        service = build_service(session)
        with pytest.raises(AppError):
            await service.overview(actor=world.leader)
        with pytest.raises(AppError):
            await service.create(
                "dialect", "mysql", RevisionWrite(content="Use backticks."), actor=world.leader
            )
        with pytest.raises(AppError):
            await service.detail("dialect", "oracle", actor=world.admin)  # type: ignore[arg-type]
        await service.create(
            "dialect", "mysql", RevisionWrite(content="Use backticks."), actor=world.admin
        )
        assert (await service.load()).dialects["mysql"].content == "Use backticks."
        overview = await service.overview(actor=world.admin)
        assert {(p.kind, p.key) for p in overview} >= {("dialect", "mysql"), ("system", "answer")}
        mysql = next(p for p in overview if p.key == "mysql")
        assert mysql.active_label == "r1" and mysql.revision_count == 1
