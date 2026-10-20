from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog, AuditRecord, AuditService
from app.core.db import Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.retention import CompositeRetentionStore, RetentionService
from app.core.storage import StoredObject, object_key
from app.modules.upload.models import UploadJob
from app.modules.upload.repository import UploadRepository
from app.modules.upload.service import build_retention_store

NOW = datetime(2026, 1, 1, tzinfo=UTC)
pytest_plugins = ["tests.test_upload_d"]
ADMIN = CurrentUser(id=1, role=Role.ADMIN)


class MemoryStorage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail = False

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.values[key] = data
        return StoredObject(key=key, size=len(data), sha256=sha256(data).hexdigest())

    async def get(self, key: str) -> bytes:
        if key not in self.values:
            raise AppError(ErrorCode.STORAGE_UNAVAILABLE)
        return self.values[key]

    async def delete(self, key: str) -> None:
        if self.fail:
            raise AppError(ErrorCode.STORAGE_UNAVAILABLE)
        self.deleted.append(key)
        self.values.pop(key, None)


async def seed(
    database: Database,
    storage: MemoryStorage,
    definitions: list[tuple[int, str, int, str, str, int]],
) -> dict[tuple[int, str, int], str]:
    keys = {}
    async with database.transaction() as session:
        for datasource, table, version, mode, status, age in definitions:
            key = object_key(1, datasource, version, f"{table}.csv")
            session.add(
                UploadJob(
                    datasource_id=datasource,
                    owner_team_id=1,
                    version=version,
                    filename=f"{table}.csv",
                    file_key=key,
                    sha256="0" * 64,
                    size=3,
                    parse_config={"table_name": table},
                    mode=mode,
                    status=status,
                    row_count=1,
                    created_by=1,
                    created_at=NOW - timedelta(days=age),
                    updated_at=NOW - timedelta(days=age),
                )
            )
            await storage.put(key, b"x\n1", "text/csv")
            keys[datasource, table, version] = key
    return keys


async def remaining(database: Database) -> list[tuple[int, int]]:
    async with database.session() as session:
        return [
            (row.datasource_id, row.version) for row in await session.scalars(select(UploadJob))
        ]


async def test_retention_preserves_current_append_chain_other_sheets_and_unfinished_jobs(
    d_database: Database,
) -> None:
    storage = MemoryStorage()
    keys = await seed(
        d_database,
        storage,
        [
            (7, "sales", 1, "overwrite", "success", 200),
            (7, "sales", 2, "append", "success", 190),
            (7, "sales", 3, "overwrite", "success", 180),
            (7, "sales", 4, "append", "success", 170),
            (7, "sales", 5, "overwrite", "failed", 160),
            (7, "sales", 6, "overwrite", "registration_pending", 150),
            (7, "sales", 7, "append", "running", 140),
            (7, "sales", 8, "append", "queued", 130),
            (7, "sales", 9, "overwrite", "preview", 120),
            (7, "returns", 10, "overwrite", "success", 110),
            (8, "sales", 1, "overwrite", "success", 200),
        ],
    )
    async with d_database.session() as session:
        store = build_retention_store(session, storage, actor=ADMIN)
        result = await RetentionService(CompositeRetentionStore([store])).run(NOW)
        assert result.removed == {"upload_job": 2, "upload_objects": 2}
        assert await store.purge(NOW) == {"upload_job": 0, "upload_objects": 0}
    assert set(storage.deleted) == {keys[7, "sales", 1], keys[7, "sales", 2]}
    assert set(await remaining(d_database)) == {(7, n) for n in range(3, 11)} | {(8, 1)}
    assert keys[7, "sales", 3] in storage.values and keys[7, "returns", 10] in storage.values
    async with d_database.session() as session:
        audits = list(await session.scalars(select(AuditLog)))
        assert len(audits) == 2
        assert all(record.payload["actor_id"] == ADMIN.id for record in audits)
        assert all(record.payload["action"] == "upload.retention" for record in audits)
        assert all("file_key" not in record.payload["diff"] for record in audits)


async def test_retention_boundary_batch_limit_and_monotonic_allocation(
    d_database: Database,
) -> None:
    storage = MemoryStorage()
    await seed(
        d_database,
        storage,
        [
            (7, "sales", 1, "overwrite", "success", 100),
            (7, "sales", 2, "overwrite", "success", 90),
            (7, "sales", 3, "overwrite", "success", 89),
            (7, "sales", 4, "overwrite", "success", 80),
            (7, "sales", 5, "overwrite", "preview", 100),
        ],
    )
    async with d_database.session() as session:
        store = build_retention_store(session, storage, actor=ADMIN, batch_size=1)
        assert (await store.purge(NOW))["upload_job"] == 1
        assert (await store.purge(NOW))["upload_job"] == 1
        assert (await store.purge(NOW))["upload_job"] == 0
        repository = UploadRepository(session)
        async with repository.transaction():
            job = await repository.allocate(
                {
                    "datasource_id": 7,
                    "owner_team_id": 1,
                    "filename": "next.csv",
                    "file_key": "tmp/team_1/ds_7/new/next.csv",
                    "sha256": "0" * 64,
                    "size": 1,
                    "parse_config": {"table_name": "sales"},
                    "mode": "overwrite",
                    "status": "preview",
                    "row_count": 0,
                },
                ADMIN.id,
            )
            assert job.version == 6
    assert set(await remaining(d_database)) == {(7, n) for n in (3, 4, 5, 6)}


async def test_storage_failure_keeps_metadata_and_retry_is_idempotent(d_database: Database) -> None:
    storage = MemoryStorage()
    await seed(
        d_database,
        storage,
        [(7, "sales", 1, "overwrite", "success", 100), (7, "sales", 2, "overwrite", "success", 95)],
    )
    storage.fail = True
    async with d_database.session() as session:
        store = build_retention_store(session, storage, actor=ADMIN)
        with pytest.raises(AppError) as error:
            await store.purge(NOW)
        assert error.value.code == ErrorCode.STORAGE_UNAVAILABLE
    assert set(await remaining(d_database)) == {(7, 1), (7, 2)}
    async with d_database.session() as session:
        assert not list(await session.scalars(select(AuditLog)))
    storage.fail = False
    async with d_database.session() as session:
        assert (await build_retention_store(session, storage, actor=ADMIN).purge(NOW))[
            "upload_job"
        ] == 1


async def test_retained_shared_object_is_not_deleted_and_foreign_key_path_is_skipped(
    d_database: Database,
) -> None:
    storage = MemoryStorage()
    keys = await seed(
        d_database,
        storage,
        [
            (7, "sales", 1, "overwrite", "success", 100),
            (7, "sales", 2, "overwrite", "success", 95),
            (7, "sales", 3, "overwrite", "success", 92),
            (8, "sales", 1, "overwrite", "success", 100),
        ],
    )
    async with d_database.transaction() as session:
        shared = await session.scalar(select(UploadJob).where(UploadJob.datasource_id == 8))
        assert shared is not None
        shared.file_key = keys[7, "sales", 1]
        foreign = await session.scalar(select(UploadJob).where(UploadJob.version == 2))
        assert foreign is not None
        foreign.file_key = "uploads/team_2/ds_7/v2/sales.csv"
    async with d_database.session() as session:
        counts = await build_retention_store(session, storage, actor=ADMIN).purge(NOW)
        assert counts == {"upload_job": 1, "upload_objects": 0}
    assert keys[7, "sales", 1] in storage.values and not storage.deleted
    assert set(await remaining(d_database)) == {(7, 2), (7, 3), (8, 1)}


async def test_audit_failure_precedes_any_object_deletion(d_database: Database) -> None:
    storage = MemoryStorage()
    await seed(
        d_database,
        storage,
        [(7, "sales", 1, "overwrite", "success", 100), (7, "sales", 2, "overwrite", "success", 95)],
    )

    class FailingAudit:
        async def append(self, record: AuditRecord) -> None:
            raise AppError(ErrorCode.TASK_FAILED)

    async with d_database.session() as session:
        store = build_retention_store(
            session, storage, actor=ADMIN, audit=AuditService(FailingAudit())
        )
        with pytest.raises(AppError):
            await store.purge(NOW)
    assert not storage.deleted and len(await remaining(d_database)) == 2


async def test_outer_rollback_keeps_retryable_metadata_after_external_deletion(
    d_database: Database,
) -> None:
    storage = MemoryStorage()
    keys = await seed(
        d_database,
        storage,
        [(7, "sales", 1, "overwrite", "success", 100), (7, "sales", 2, "overwrite", "success", 95)],
    )
    async with d_database.session() as session:
        await session.begin()
        # Start the SQLite outer write transaction before creating a nested savepoint.
        await session.execute(
            UploadJob.__table__.update().values(
                size=UploadJob.size, updated_at=UploadJob.updated_at
            )
        )
        assert (await build_retention_store(session, storage, actor=ADMIN).purge(NOW))[
            "upload_job"
        ] == 1
        await session.rollback()
    assert len(await remaining(d_database)) == 2
    assert keys[7, "sales", 1] not in storage.values
    assert keys[7, "sales", 2] in storage.values
    async with d_database.session() as session:
        assert (await build_retention_store(session, storage, actor=ADMIN).purge(NOW))[
            "upload_job"
        ] == 1


@pytest.mark.parametrize(
    "actor",
    [CurrentUser(id=2, role=Role.LEADER, team_id=1), ADMIN.model_copy(update={"active": False})],
)
async def test_retention_requires_live_scheduler_admin(
    d_database: Database, actor: CurrentUser
) -> None:
    storage = MemoryStorage()
    async with d_database.session() as session:
        with pytest.raises(AppError):
            await build_retention_store(session, storage, actor=actor).purge(NOW)
    assert not storage.deleted


async def test_retention_rejects_invalid_policy_and_naive_clock(d_database: Database) -> None:
    async with d_database.session() as session:
        with pytest.raises(AppError):
            build_retention_store(session, MemoryStorage(), actor=ADMIN, retention_days=0)
        with pytest.raises(AppError):
            await build_retention_store(session, MemoryStorage(), actor=ADMIN).purge(
                datetime(2026, 1, 1)
            )
