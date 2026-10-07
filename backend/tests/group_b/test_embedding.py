import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError
from app.core.llm import EmbeddingClient, ModelConfig, ModelRegistry
from app.core.scope import ScopeLevel
from app.core.tasks import TaskManager
from app.modules.embedding.models import EmbeddingItem
from app.modules.embedding.repository import EmbeddingRepository
from app.modules.embedding.schemas import IndexDocument, IndexReference, SearchInput
from app.modules.embedding.service import EmbeddingService
from tests.fakes import FakeModels, FakeTransport, FakeUsage, MemoryQueue, MemoryTaskStore


class Actors:
    async def get_actor(self, identifier):
        return CurrentUser(
            id=identifier,
            role=Role.ADMIN if identifier == 1 else Role.MEMBER,
            team_id=None if identifier == 1 else 1,
        )


class Source:
    def __init__(self):
        self.document_value = IndexDocument(
            kind="column",
            ref_id=1,
            datasource_id=1,
            text="Order number",
            references={"orders": {"id"}},
            scope=ScopeLevel.PLATFORM,
            status="active",
        )

    async def document(self, ref, actor):
        return self.document_value

    async def catalog(self, datasource_id):
        return [IndexReference(kind="column", ref_id=1, datasource_id=1, actor_id=1)]


def service_for(b_services):
    transport = FakeTransport()
    source = Source()
    client = EmbeddingClient(
        ModelRegistry(
            FakeModels(
                [
                    ModelConfig(
                        id="embed",
                        kind="embedding",
                        model="fake",
                        api_key=SecretStr("fake"),
                        retries=0,
                    )
                ]
            )
        ),
        FakeUsage(),
        transport,
    )
    service = EmbeddingService(
        EmbeddingRepository(b_services.session),
        b_services.permissions,
        client,
        source,
        Actors(),
        AuditService(DatabaseAuditStore(b_services.session)),
        dimensions=2,
    )
    store = MemoryTaskStore()
    manager = TaskManager(
        store,
        MemoryQueue(),
        {service.UPDATE_TASK: service.update_task, service.REBUILD_TASK: service.rebuild_task},
    )
    return service, manager, source, transport


async def test_async_outbox_update_rebuild_and_live_authorization(b_services, admin, member):
    service, manager, source, _ = service_for(b_services)
    await service.mark_dirty("column", 1, 1, actor=admin)
    row = await b_services.session.scalar(select(EmbeddingItem))
    assert row.dirty and row.embedding is None
    task = await service.enqueue(1, manager, actor=admin)
    await manager.run(task.id)
    await b_services.session.refresh(row)
    assert row.model_id == "embed" and row.embedding == [0.1, 0.2] and not row.dirty
    assert len(await service.search(SearchInput(datasource_id=1, query="Order"), member)) == 1
    source.document_value = source.document_value.model_copy(
        update={"scope": ScopeLevel.PERSONAL, "owner_user_id": 99}
    )
    assert await service.search(SearchInput(datasource_id=1, query="Order"), member) == []
    source.document_value = source.document_value.model_copy(
        update={"scope": ScopeLevel.PLATFORM, "owner_user_id": None}
    )
    rebuild = await service.enqueue(1, manager, actor=admin, rebuild=True)
    await manager.run(rebuild.id)
    assert (await manager.store.get(rebuild.id)).status == "success"
    b_services.provider.grants[0].member_columns = frozenset({"phone"})
    assert await service.search(SearchInput(datasource_id=1, query="Order"), member) == []


async def test_failed_rebuild_keeps_old_vectors(b_services, admin):
    service, manager, source, transport = service_for(b_services)
    await service.mark_dirty("column", 1, 1, actor=admin)
    task = await service.enqueue(1, manager, actor=admin)
    await manager.run(task.id)
    original = await b_services.session.scalar(select(EmbeddingItem))
    before = original.embedding
    transport.fail_models.add("embed")
    task = await service.enqueue(1, manager, actor=admin, rebuild=True)
    await manager.run(task.id)
    assert (await manager.store.get(task.id)).status == "failed"
    await b_services.session.refresh(original)
    assert original.embedding == before and original.model_id == "embed"


async def test_revision_prevents_stale_write_and_dimension_mismatch(b_services, admin, member):
    service, manager, source, transport = service_for(b_services)
    with pytest.raises(AppError):
        await service.enqueue(1, manager, actor=member)
    await service.mark_dirty("column", 1, 1, actor=admin)
    pending = (await service.repository.pending(1))[0]
    old_revision = pending.revision
    await service.mark_dirty("column", 1, 1, actor=admin)
    assert not await service.repository.save(
        source.document_value, [0.1, 0.2], "embed", old_revision
    )
    service.dimensions = 1024
    task = await service.enqueue(1, manager, actor=admin)
    await manager.run(task.id)
    assert (await manager.store.get(task.id)).status == "failed"
    await b_services.session.refresh(pending)
    assert pending.embedding is None
