import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.response import PageParams
from app.modules.modelcfg.models import ModelConfiguration
from app.modules.modelcfg.schemas import ModelWrite
from app.modules.modelcfg.service import build_service
from tests.fakes import FakeTransport, FakeUsage


def config(kind="llm", enabled=True):
    return ModelWrite(
        type=kind,
        provider="openai",
        model_name="test",
        api_key=SecretStr("model-secret"),
        enabled=enabled,
        embedding_dimension=1024 if kind == "embedding" else None,
    )


class ModelTestTransport(FakeTransport):
    async def embed(self, model, texts):
        vectors, usage = await super().embed(model, texts)
        return [vector * 512 for vector in vectors], usage


async def test_three_model_types_preferences_connection_and_secret_masks(identity_db):
    world = identity_db
    transport, usage = ModelTestTransport(), FakeUsage()
    async with world.database.transaction() as session:
        service = build_service(session, world.box, transport, usage)
        # No model of a kind yet reads as "not set", not as an outage.
        empty = await service.get_preference(world.team, "rerank", actor=world.leader)
        assert empty.model_id is None
        for kind in ("llm", "embedding", "rerank"):
            created = await service.create(config(kind), actor=world.admin)
            assert created.api_key == "********"
            row = await session.get(ModelConfiguration, created.id)
            assert "model-secret" not in row.api_key_enc
            await service.set_preference(world.team, kind, created.id, actor=world.leader)
            selected = await service.registry.select(world.team, kind)
            assert selected.id == str(created.id)
            assert selected.api_key.get_secret_value() == "model-secret"
            assert (await service.test(created.id, actor=world.admin)).available
        assert len(usage.records) == 3
        assert {record.model_id for record in usage.records} == {"1", "2", "3"}
        assert all(record.prompt_version == "answer:v3" for record in usage.records)
        listing = await service.list(PageParams(), actor=world.leader)
        assert "model-secret" not in listing.model_dump_json()
        audits = list(await session.scalars(select(AuditLog)))
        assert {"model.create", "model.preference", "model.test"} <= {
            r.payload["action"] for r in audits
        }


async def test_model_roles_disabled_selection_dimensions_and_sanitized_test_failure(identity_db):
    world = identity_db
    transport = FakeTransport()
    async with world.database.transaction() as session:
        service = build_service(session, world.box, transport)
        with pytest.raises(AppError):
            await service.create(config(), actor=world.leader)
        with pytest.raises(AppError):
            await service.list(PageParams(), actor=world.member)
        disabled = await service.create(config(enabled=False), actor=world.admin)
        with pytest.raises(AppError):
            await service.set_preference(world.team, "llm", disabled.id, actor=world.leader)
        with pytest.raises(AppError):
            await service.set_preference(world.other, "llm", disabled.id, actor=world.leader)
        transport.fail_models.add(str(disabled.id))
        assert not (await service.test(disabled.id, actor=world.admin)).available
        with pytest.raises(AppError):
            await service.test(disabled.id, actor=world.leader)
        embed = await service.create(config("embedding"), actor=world.admin)
        with pytest.raises(AppError):
            await service.update(
                embed.id,
                config("embedding").model_copy(update={"embedding_dimension": 3}),
                actor=world.admin,
            )
        with pytest.raises(AppError):
            await service.create(
                config("embedding").model_copy(update={"embedding_dimension": 3}), actor=world.admin
            )
        llm = await service.create(config(), actor=world.admin)
        await service.set_preference(world.team, "llm", llm.id, actor=world.leader)
        with pytest.raises(AppError):
            await service.update(llm.id, config(enabled=False), actor=world.admin)
        # The switch toggles without resending the key, under the same rules.
        with pytest.raises(AppError):
            await service.set_enabled(llm.id, False, actor=world.admin)
        with pytest.raises(AppError):
            await service.set_enabled(disabled.id, True, actor=world.leader)
        assert (await service.set_enabled(disabled.id, True, actor=world.admin)).enabled


async def test_unsaved_model_configuration_can_be_tested_without_storing_anything(identity_db):
    world = identity_db
    transport, usage = ModelTestTransport(), FakeUsage()
    async with world.database.transaction() as session:
        service = build_service(session, world.box, transport, usage)
        assert (await service.test_draft(config("llm"), actor=world.admin)).available
        assert (await service.list(PageParams(), actor=world.admin)).total == 0
        with pytest.raises(AppError):
            await service.test_draft(config("llm"), actor=world.leader)
        audits = list(await session.scalars(select(AuditLog)))
        assert "model-secret" not in " ".join(str(r.payload) for r in audits)


async def test_every_user_can_list_enabled_llm_choices_without_endpoints_or_keys(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        service = build_service(session, world.box, FakeTransport(), FakeUsage())
        first = await service.create(config("llm"), actor=world.admin)
        await service.create(config("llm", enabled=False), actor=world.admin)
        await service.create(config("embedding"), actor=world.admin)
        choices = await service.choices(actor=world.member)
        assert [c.id for c in choices] == [first.id] and choices[0].is_default
        dumped = choices[0].model_dump_json()
        assert "model-secret" not in dumped and "base_url" not in dumped
