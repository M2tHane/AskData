import asyncio
import base64
import binascii
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.datasource import ColumnMetadata, Datasource, TableMetadata
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.importer import ImportGateway, ImportPlan
from app.core.permission import PermissionService
from app.core.storage import ObjectStore, object_key, team_segment
from app.core.tasks import TaskContext, TaskManager
from app.modules.upload.parsing import (
    clean_name,
    configured_columns,
    convert_rows,
    parse_rows,
    validate_file,
)
from app.modules.upload.repository import UploadRepository
from app.modules.upload.schemas import (
    ConfirmRequest,
    JobView,
    ParseConfig,
    Preview,
    PreviewRequest,
    UploadHistoryRecord,
    UploadRetentionPolicy,
)


class UploadRegistration(Protocol):
    """Adapter calls datasource/schema/permission services, never their repositories.

    reserve creates an ungranted pending upload source. publish registers encrypted
    query credentials and metadata; ONLY the newly created source's owner team is
    granted. Existing source updates leave new columns ungranted and invalidate
    removed-column grants. Implementations must be idempotent for job_id.
    """

    async def reserve(self, name: str, team_id: int | None, actor: CurrentUser) -> int: ...
    async def owner_team(self, datasource_id: int) -> int | None: ...
    async def publish(
        self,
        job_id: int,
        datasource: Datasource,
        table: TableMetadata,
        owner_team_id: int | None,
        actor: CurrentUser,
    ) -> None: ...


class UploadRetentionStore:
    """Purge expired, provably superseded source backups through core storage.

    Keep each table's latest successful overwrite and every subsequent append, all
    unfinished/failed jobs, and the highest allocated version. Deletion is retried
    using retained metadata if object storage fails or the DB transaction rolls back.
    """

    def __init__(
        self,
        repository: UploadRepository,
        storage: ObjectStore,
        audit: AuditService,
        actor: CurrentUser,
        policy: UploadRetentionPolicy,
    ) -> None:
        self.repository, self.storage, self.audit = repository, storage, audit
        self.actor, self.policy = actor, policy

    @staticmethod
    def _obsolete(rows: list[UploadHistoryRecord]) -> list[UploadHistoryRecord]:
        if not rows:
            return []
        by_table: dict[str, list[UploadHistoryRecord]] = {}
        for row in rows:
            table = row.parse_config.get("table_name")
            if not isinstance(table, str) or not table:
                # Unknown legacy metadata cannot prove which backup is current.
                return []
            if row.status == "success":
                if row.mode not in {"overwrite", "append"}:
                    return []
                by_table.setdefault(table, []).append(row)
        protected = {max(rows, key=lambda row: row.version).id}
        for versions in by_table.values():
            for row in sorted(versions, key=lambda item: item.version, reverse=True):
                protected.add(row.id)
                if row.mode == "overwrite":
                    break
        return [row for row in rows if row.status == "success" and row.id not in protected]

    async def purge(self, before: datetime) -> dict[str, int]:
        if not self.actor.active or self.actor.role != Role.ADMIN:
            raise PermissionDenied()
        if before.tzinfo is None:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        expired_at = before.astimezone(UTC) - timedelta(days=self.policy.retention_days)
        removed = {"upload_job": 0, "upload_objects": 0}
        async with self.repository.retention_transaction():
            for datasource_id in await self.repository.retention_sources(expired_at):
                rows = await self.repository.locked_history(datasource_id)
                for row in self._obsolete(rows):
                    updated = row.updated_at
                    if updated.tzinfo is None:  # SQLite's timezone storage in local tests.
                        updated = updated.replace(tzinfo=UTC)
                    if updated > expired_at:
                        continue
                    # Never allow an inconsistent row to delete another team's object,
                    # another version, or a currently reusable temporary upload.
                    if row.file_key != object_key(
                        row.owner_team_id, row.datasource_id, row.version, row.filename
                    ):
                        continue
                    shared = await self.repository.other_file_references(row.file_key, row.id)
                    await self.audit.record(
                        self.actor,
                        "upload.retention",
                        ("upload_job", str(row.id)),
                        {"datasource_id": row.datasource_id, "version": row.version},
                    )
                    if not shared:
                        try:
                            await self.storage.delete(row.file_key)
                        except AppError:
                            raise
                        except Exception:
                            raise AppError(ErrorCode.STORAGE_UNAVAILABLE, http_status=503) from None
                        removed["upload_objects"] += 1
                    await self.repository.delete_history(row.id)
                    removed["upload_job"] += 1
                    if removed["upload_job"] >= self.policy.batch_size:
                        return removed
        return removed


def build_retention_store(
    session: AsyncSession,
    storage: ObjectStore,
    *,
    actor: CurrentUser,
    retention_days: int = 90,
    batch_size: int = 100,
    audit: AuditService | None = None,
) -> UploadRetentionStore:
    """Public factory for core CompositeRetentionStore; scheduler resolves a real admin."""
    try:
        policy = UploadRetentionPolicy(retention_days=retention_days, batch_size=batch_size)
    except ValidationError:
        raise AppError(ErrorCode.VALIDATION_ERROR) from None
    return UploadRetentionStore(
        UploadRepository(session),
        storage,
        audit or AuditService(DatabaseAuditStore(session)),
        actor,
        policy,
    )


class UploadService:
    def __init__(
        self,
        repository: UploadRepository,
        storage: ObjectStore,
        importer: ImportGateway,
        registration: UploadRegistration,
        permissions: PermissionService,
        audit: AuditService,
        tasks: TaskManager,
    ) -> None:
        self.repository, self.storage, self.importer = repository, storage, importer
        self.registration, self.permissions, self.audit = registration, permissions, audit
        self.tasks = tasks

    @staticmethod
    def _role(actor: CurrentUser) -> None:
        if not actor.active or actor.role not in {Role.ADMIN, Role.LEADER}:
            raise PermissionDenied()

    async def _source_access(self, datasource_id: int, actor: CurrentUser) -> int | None:
        self._role(actor)
        scope = await self.permissions.visible_scope(actor, datasource_id)
        owner = await self.registration.owner_team(datasource_id)
        if actor.role != Role.ADMIN and (owner != actor.team_id or not scope.tables):
            raise PermissionDenied()
        return owner

    async def preview(self, request: PreviewRequest, *, actor: CurrentUser) -> Preview:
        self._role(actor)
        try:
            data = base64.b64decode(request.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise AppError(ErrorCode.VALIDATION_ERROR) from None
        validate_file(request.filename, data)
        headers, rows, sheets, warnings = await asyncio.to_thread(
            parse_rows,
            request.filename,
            data,
            request.config,
            100,
        )
        columns, suspicious = configured_columns(request.config, headers, rows)
        config = request.config.model_copy(
            update={
                "columns": columns,
                "table_name": clean_name(request.config.table_name, "data"),
            }
        )
        async with self.repository.transaction():
            if request.datasource_id is None:
                # Leaders own what they create; admins create team-less sources and choose
                # the visible teams on the datasource afterwards.
                team = actor.team_id if actor.role == Role.LEADER else request.owner_team_id
                datasource_id = await self.registration.reserve(
                    request.datasource_name, team, actor
                )
            else:
                datasource_id = request.datasource_id
                team = await self._source_access(datasource_id, actor)
            key = f"tmp/{team_segment(team)}/ds_{datasource_id}/{uuid4()}/{request.filename}"
            stored = await self.storage.put(key, data, "application/octet-stream")
            job = await self.repository.allocate(
                {
                    "datasource_id": datasource_id,
                    "owner_team_id": team,
                    "file_key": key,
                    "filename": request.filename,
                    "sha256": stored.sha256,
                    "size": stored.size,
                    "parse_config": config.model_dump(mode="json"),
                    "status": "preview",
                    "mode": "overwrite",
                    "row_count": 0,
                },
                actor.id,
            )
            await self.audit.record(actor, "upload.preview", ("upload_job", str(job.id)))
        return Preview(
            id=job.id,
            datasource_id=datasource_id,
            columns=columns,
            rows=rows,
            sheets=sheets,
            warnings=warnings + suspicious,
            config=config,
        )

    async def add_sheet(
        self, identifier: int, config: ParseConfig, *, actor: CurrentUser
    ) -> Preview:
        """Preview another sheet of an uploaded workbook as its own job (one table per sheet).

        The stored file is copied server-side, so the browser never re-uploads it and each job
        owns its temporary object (the importer deletes it after success).
        """
        self._role(actor)
        async with self.repository.transaction():
            await self._job_access(identifier, actor)
            job = await self.repository.required(identifier)
            if job.status != "preview":
                raise AppError(ErrorCode.CONFLICT)
            data = await self.storage.get(job.file_key)
            headers, rows, sheets, warnings = await asyncio.to_thread(
                parse_rows, job.filename, data, config, 100
            )
            columns, suspicious = configured_columns(config, headers, rows)
            normalized = config.model_copy(
                update={"columns": columns, "table_name": clean_name(config.table_name, "data")}
            )
            key = (
                f"tmp/{team_segment(job.owner_team_id)}/ds_{job.datasource_id}"
                f"/{uuid4()}/{job.filename}"
            )
            stored = await self.storage.put(key, data, "application/octet-stream")
            sibling = await self.repository.allocate(
                {
                    "datasource_id": job.datasource_id,
                    "owner_team_id": job.owner_team_id,
                    "file_key": key,
                    "filename": job.filename,
                    "sha256": stored.sha256,
                    "size": stored.size,
                    "parse_config": normalized.model_dump(mode="json"),
                    "status": "preview",
                    "mode": "overwrite",
                    "row_count": 0,
                },
                actor.id,
            )
            await self.audit.record(actor, "upload.preview", ("upload_job", str(sibling.id)))
        return Preview(
            id=sibling.id,
            datasource_id=job.datasource_id,
            columns=columns,
            rows=rows,
            sheets=sheets,
            warnings=warnings + suspicious,
            config=normalized,
        )

    async def _job_access(self, identifier: int, actor: CurrentUser) -> None:
        self._role(actor)
        job = await self.repository.required(identifier)
        await self.permissions.visible_scope(actor, job.datasource_id)
        if actor.role != Role.ADMIN and (
            job.owner_team_id != actor.team_id or job.created_by != actor.id
        ):
            raise PermissionDenied()

    async def confirm(
        self,
        identifier: int,
        request: ConfirmRequest,
        *,
        actor: CurrentUser,
    ) -> JobView:
        async with self.repository.transaction():
            await self._job_access(identifier, actor)
            job = await self.repository.required(identifier, lock=True)
            if job.status != "preview":
                raise AppError(ErrorCode.CONFLICT)
            data = await self.storage.get(job.file_key)
            headers, rows, _, _ = await asyncio.to_thread(
                parse_rows,
                job.filename,
                data,
                request.config,
                100,
            )
            columns, _ = configured_columns(request.config, headers, rows, enforce=True)
            config = request.config.model_copy(
                update={
                    "columns": columns,
                    "table_name": clean_name(request.config.table_name, "data"),
                }
            )
            await self.repository.update(
                job,
                {
                    "parse_config": config.model_dump(mode="json"),
                    "mode": request.mode,
                    "status": "queued",
                },
            )
            await self.audit.record(actor, "upload.confirm", ("upload_job", str(job.id)))
        # Job and audit commit before queue delivery can start the handler.
        try:
            task = await self.tasks.enqueue(
                "upload.import",
                {
                    "job_id": identifier,
                    "actor": actor.model_dump(mode="json"),
                },
            )
        except AppError:
            async with self.repository.transaction():
                job = await self.repository.required(identifier, lock=True)
                await self.repository.update(job, {"status": "failed", "error": "TASK_FAILED"})
            raise AppError(ErrorCode.TASK_FAILED, http_status=503) from None
        async with self.repository.transaction():
            job = await self.repository.required(identifier, lock=True)
            await self.repository.update(job, {"task_id": task.id})
            return JobView.model_validate(job)

    async def get(self, identifier: int, *, actor: CurrentUser) -> JobView:
        async with self.repository.transaction():
            await self._job_access(identifier, actor)
            return JobView.model_validate(await self.repository.required(identifier))

    async def history(
        self,
        datasource_id: int,
        offset: int,
        limit: int,
        *,
        actor: CurrentUser,
    ) -> list[JobView]:
        async with self.repository.transaction():
            await self._source_access(datasource_id, actor)
            return [
                JobView.model_validate(job)
                for job in await self.repository.history(datasource_id, offset, limit)
            ]

    async def run_import(self, context: TaskContext) -> None:
        identifier = int(context.task.payload["job_id"])
        actor = CurrentUser.model_validate(context.task.payload["actor"])
        try:
            async with self.repository.transaction():
                job = await self.repository.required(identifier, lock=True)
                if job.status == "success":
                    return
                if job.status != "queued":
                    raise AppError(ErrorCode.CONFLICT)
                await self._job_access(identifier, actor)
                await self.repository.update(job, {"status": "running"})
                filename, key, config = job.filename, job.file_key, job.parse_config
            data = await self.storage.get(key)
            if hashlib.sha256(data).hexdigest() != job.sha256:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            parse_config = ParseConfig.model_validate(config)
            headers, rows, _, _ = await asyncio.to_thread(parse_rows, filename, data, parse_config)
            columns, _ = configured_columns(parse_config, headers, rows, enforce=True)
            converted = await asyncio.to_thread(convert_rows, rows, columns)
            plan = ImportPlan.model_validate(
                {
                    "datasource_id": job.datasource_id,
                    "table_name": parse_config.table_name,
                    "columns": columns,
                    "mode": job.mode,
                }
            )
            await context.progress(10)
            permanent = object_key(job.owner_team_id, job.datasource_id, job.version, filename)
            # Pre-stage the immutable backup. A failed import is never marked published.
            await self.storage.put(permanent, data, "application/octet-stream")
            async with self.importer.transaction(plan) as writer:
                count = await writer.write(converted)
            # Separate databases cannot share a local transaction. On a publish failure
            # retain status 'registration_pending'; retry publication without repeating COPY.
            async with self.repository.transaction():
                job = await self.repository.required(identifier, lock=True)
                await self.repository.update(
                    job,
                    {
                        "file_key": permanent,
                        "row_count": count,
                        "status": "registration_pending",
                    },
                )
            await self.publish(identifier, actor=actor)
            await self.storage.delete(key)
            await context.progress(100)
        except (Exception, asyncio.CancelledError) as error:
            code = (
                error.code
                if isinstance(error, AppError)
                else ErrorCode.QUERY_CANCELLED
                if isinstance(error, asyncio.CancelledError)
                else ErrorCode.TASK_FAILED
            )
            async with self.repository.transaction():
                job = await self.repository.required(identifier, lock=True)
                if job.status != "registration_pending":
                    await self.repository.update(job, {"status": "failed", "error": code})
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AppError(code) from None

    async def publish(self, identifier: int, *, actor: CurrentUser) -> JobView:
        """Recover registration after COPY commits; never repeats an append."""
        async with self.repository.transaction():
            await self._job_access(identifier, actor)
            job = await self.repository.required(identifier, lock=True)
            if job.status != "registration_pending":
                raise AppError(ErrorCode.CONFLICT)
            plan = ImportPlan.model_validate(
                {
                    "datasource_id": job.datasource_id,
                    "table_name": job.parse_config["table_name"],
                    "columns": job.parse_config["columns"],
                    "mode": job.mode,
                }
            )
            await self.registration.publish(
                job.id,
                self.importer.query_datasource(job.datasource_id),
                TableMetadata(
                    schema_name=plan.schema_name,
                    name=plan.table_name,
                    comment=str(job.parse_config.get("table_comment") or ""),
                    columns=[
                        ColumnMetadata(
                            name=c.name, data_type=c.data_type, comment=c.description, ordinal=i + 1
                        )
                        for i, c in enumerate(plan.columns)
                    ],
                ),
                job.owner_team_id,
                actor,
            )
            await self.permissions.invalidate(job.datasource_id)
            await self.repository.update(job, {"status": "success", "error": None})
            await self.audit.record(actor, "upload.publish", ("upload_job", str(job.id)))
            return JobView.model_validate(job)
