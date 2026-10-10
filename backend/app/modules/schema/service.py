from typing import Annotated, Any

from fastapi import Depends, Request

from app.core.audit import AuditService
from app.core.crypto import SecretBox
from app.core.datasource import DatasourceInspector, MetadataSnapshot, TableMetadata
from app.core.db import AsyncSession, Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.permission import PermissionService, ScopeCache
from app.core.tasks import TaskContext, TaskHandler, TaskManager, TaskRecord
from app.modules.auth.service import audit_for, get_session, require_admin, secret_box, settings_for
from app.modules.datasource.service import DatasourceService
from app.modules.datasource.service import build_service as build_datasource
from app.modules.embedding.service import build_outbox
from app.modules.schema.repository import SchemaRepository
from app.modules.schema.schemas import ColumnRead, RelationRead
from app.modules.schema.schemas import SyncResult as SyncResult
from app.modules.schema.schemas import TableRead as TableRead

SYNC_TASK = "schema_sync"


class SchemaService:
    def __init__(
        self, repository: SchemaRepository, datasource: DatasourceService, audit: AuditService
    ) -> None:
        self.repository, self.datasource, self.audit = repository, datasource, audit
        self.index = build_outbox(repository.session)

    async def catalog(self, datasource_id: int) -> list[TableRead]:
        """Internal complete metadata for core authorization; public browsing uses browse."""
        return await self.repository.catalog(datasource_id)

    async def lock_catalog(self, datasource_id: int) -> None:
        await self.datasource.lock(datasource_id)
        await self.repository.lock(datasource_id)

    async def browse(
        self, datasource_id: int, permissions: PermissionService, *, actor: CurrentUser
    ) -> list[TableRead]:
        if actor.role == Role.ADMIN and actor.active:
            # Admins see every column anyway; disabled sources stay manageable.
            await self.datasource.require_managed(datasource_id)
            return await self.catalog(datasource_id)
        scope = await permissions.visible_scope(actor, datasource_id)
        result = []
        for table in await self.catalog(datasource_id):
            visible = scope.tables.get(table.qualified_name)
            if visible:
                result.append(
                    table.model_copy(
                        update={"columns": [c for c in table.columns if c.name in visible.columns]}
                    )
                )
        return result

    async def relations(
        self, datasource_id: int, permissions: PermissionService, *, actor: CurrentUser
    ) -> list[RelationRead]:
        tables = await self.browse(datasource_id, permissions, actor=actor)
        ids = [c.id for table in tables for c in table.columns]
        return [RelationRead.model_validate(row) for row in await self.repository.relations(ids)]

    async def describe_table(
        self, target_id: int, description: str, *, actor: CurrentUser
    ) -> TableRead:
        require_admin(actor)
        row = await self.repository.tables.get(target_id)
        if row is None or row.status != "active":
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.repository.tables.update(row, {"description": description})
        await self.index.mark_dirty("table", row.id, row.datasource_id, actor=actor)
        await self.audit.record(actor, "schema.table.describe", ("meta_table", str(target_id)))
        return TableRead.model_validate(row)

    async def describe_column(
        self, target_id: int, description: str, *, actor: CurrentUser
    ) -> ColumnRead:
        require_admin(actor)
        row = await self.repository.columns.get(target_id)
        if row is None or row.status != "active":
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.repository.columns.update(row, {"description": description})
        table = await self.repository.tables.get(row.table_id)
        if table is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.index.mark_dirty("column", row.id, table.datasource_id, actor=actor)
        await self.audit.record(actor, "schema.column.describe", ("meta_column", str(target_id)))
        return ColumnRead.model_validate(row)

    async def synchronize(
        self, datasource_id: int, grants: Any, *, actor: CurrentUser
    ) -> SyncResult:
        require_admin(actor)
        connection = await self.datasource.connection(datasource_id)
        snapshot = await self.datasource.inspector.metadata(connection)
        await self.lock_catalog(datasource_id)
        result = await self.repository.synchronize(datasource_id, snapshot, actor.id)
        await grants.expire_metadata(result)
        for table in await self.catalog(datasource_id):
            await self.index.mark_dirty("table", table.id, datasource_id, actor=actor)
            for column in table.columns:
                await self.index.mark_dirty("column", column.id, datasource_id, actor=actor)
        for kind, identifiers in (
            ("table", result.removed_tables),
            ("column", result.removed_columns),
        ):
            for identifier in identifiers:
                await self.index.mark_dirty(kind, identifier, datasource_id, actor=actor)
        await self.audit.record(
            actor,
            "schema.sync",
            ("datasource", str(datasource_id)),
            {
                "table_count": result.table_count,
                "removed_column_count": len(result.removed_columns),
            },
        )
        return result

    async def synchronize_upload(
        self, datasource_id: int, table: TableMetadata, *, actor: CurrentUser
    ) -> SyncResult:
        owner = await self.datasource.upload_owner(datasource_id)
        self.datasource.require_upload_owner(owner, actor)
        if table.schema_name != f"ds_{datasource_id}":
            raise AppError(ErrorCode.VALIDATION_ERROR)
        await self.lock_catalog(datasource_id)
        result = await self.repository.synchronize(
            datasource_id, MetadataSnapshot(tables=[table]), actor.id, partial=True
        )
        for metadata in await self.catalog(datasource_id):
            if metadata.schema_name == table.schema_name and metadata.name == table.name:
                await self.index.mark_dirty("table", metadata.id, datasource_id, actor=actor)
                for column in metadata.columns:
                    await self.index.mark_dirty("column", column.id, datasource_id, actor=actor)
        for identifier in result.removed_columns:
            await self.index.mark_dirty("column", identifier, datasource_id, actor=actor)
        await self.audit.record(actor, "schema.upload.sync", ("datasource", str(datasource_id)))
        return result

    async def enqueue(
        self, datasource_id: int, manager: TaskManager, *, actor: CurrentUser
    ) -> TaskRecord:
        require_admin(actor)
        await self.datasource.connection(datasource_id)
        await self.audit.record(actor, "schema.sync.enqueue", ("datasource", str(datasource_id)))
        return await manager.enqueue(
            SYNC_TASK, {"datasource_id": datasource_id, "actor_id": actor.id}
        )

    async def task(self, task_id: str, manager: TaskManager, *, actor: CurrentUser) -> TaskRecord:
        require_admin(actor)
        result = await manager.store.get(task_id)
        if result is None or result.type != SYNC_TASK:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return result


def build_service(
    session: AsyncSession, box: SecretBox, inspector: DatasourceInspector | None = None
) -> SchemaService:
    return SchemaService(
        SchemaRepository(session), build_datasource(session, box, inspector), audit_for(session)
    )


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session, scope="function")]
) -> SchemaService:
    return build_service(
        session,
        secret_box(settings_for(request)),
        getattr(request.app.state, "datasource_inspector", None),
    )


def get_permissions(
    request: Request, session: Annotated[AsyncSession, Depends(get_session, scope="function")]
) -> PermissionService:
    from app.modules.permission.service import build_service as build_permission

    return build_permission(
        session, secret_box(settings_for(request)), getattr(request.app.state, "scope_cache", None)
    ).permissions


def get_task_manager(request: Request) -> TaskManager:
    manager: TaskManager | None = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise AppError(ErrorCode.TASK_FAILED, http_status=503)
    return manager


def sync_handler(
    database: Database,
    box: SecretBox,
    cache: ScopeCache | None = None,
    inspector: DatasourceInspector | None = None,
) -> TaskHandler:
    async def run(context: TaskContext) -> None:
        from app.modules.org.service import build_service as build_org
        from app.modules.permission.service import build_service as build_permission

        await context.progress(10)
        async with database.transaction() as session:
            actor = await build_org(session).identity(int(context.task.payload["actor_id"]))
            service = build_service(session, box, inspector)
            grants = build_permission(session, box, cache)
            result = await service.synchronize(
                int(context.task.payload["datasource_id"]), grants, actor=actor
            )
            # Newly discovered tables and columns start visible to every active team and
            # its employees; administrators narrow them afterwards on the datasource page.
            await grants.apply_inherited_defaults(result, actor=actor)
        await grants.permissions.invalidate(int(context.task.payload["datasource_id"]))
        await context.progress(100)

    return run


async def enqueue_daily(
    service: SchemaService, manager: TaskManager, *, actor: CurrentUser
) -> list[TaskRecord]:
    require_admin(actor)
    return [
        await service.enqueue(identifier, manager, actor=actor)
        for identifier in await service.datasource.active_ids()
    ]
