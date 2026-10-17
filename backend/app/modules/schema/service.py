from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import Request

from app.core.audit import AuditService
from app.core.crypto import SecretBox
from app.core.datasource import DatasourceInspector, MetadataSnapshot, TableMetadata
from app.core.db import AsyncSession, Database
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.permission import PermissionService, ScopeCache
from app.core.tasks import TaskContext, TaskHandler, TaskManager, TaskRecord
from app.modules.auth.service import audit_for, require_admin
from app.modules.datasource.service import DatasourceService
from app.modules.datasource.service import build_service as build_datasource
from app.modules.embedding.service import build_outbox
from app.modules.schema.repository import SchemaRepository
from app.modules.schema.schemas import (
    ColumnRead,
    DraftRequest,
    DraftResult,
    RelationRead,
    SetupRead,
    SetupResult,
    SetupSummary,
    SetupWrite,
)
from app.modules.schema.schemas import SyncResult as SyncResult
from app.modules.schema.schemas import TableRead as TableRead

SYNC_TASK = "schema_sync"


class DescriptionDrafter(Protocol):
    """Proposes semantics for columns from their names and neighbours (a model call)."""

    async def draft(
        self, table: TableRead, columns: list[ColumnRead], *, actor: CurrentUser
    ) -> dict[int, str]: ...


def _semantic(text: str | None) -> str:
    return " ".join((text or "").split())


class SchemaService:
    def __init__(
        self,
        repository: SchemaRepository,
        datasource: DatasourceService,
        audit: AuditService,
        drafter: DescriptionDrafter | None = None,
    ) -> None:
        self.repository, self.datasource, self.audit = repository, datasource, audit
        self.drafter = drafter
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
        self,
        datasource_id: int,
        grants: Any,
        *,
        actor: CurrentUser,
        progress: Callable[[int], Awaitable[None]] | None = None,
    ) -> SyncResult:
        require_admin(actor)
        connection = await self.datasource.connection(datasource_id)
        strict = await self.datasource.requires_semantics(datasource_id)
        if progress:
            await progress(25)
        # Tables, columns, their comments and foreign keys, in one read of the database.
        snapshot = await self.datasource.inspector.metadata(connection)
        if progress:
            await progress(70)
        await self.lock_catalog(datasource_id)
        result = await self.repository.synchronize(datasource_id, snapshot, actor.id, strict=strict)
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
                "pending_tables": len(result.pending_tables),
                "pending_columns": len(result.pending_columns),
            },
        )
        return result

    # --- setup: choose tables, give every column a semantic ---------------------------------

    async def setup(self, datasource_id: int, *, actor: CurrentUser) -> SetupRead:
        require_admin(actor)
        await self.datasource.require_managed(datasource_id)
        tables, columns = await self.repository.setup_rows(datasource_id)
        return SetupRead(
            datasource_id=datasource_id,
            require_semantics=await self.datasource.requires_semantics(datasource_id),
            tables=[
                TableRead.model_validate(table).model_copy(
                    update={
                        "columns": [
                            ColumnRead.model_validate(c) for c in columns if c.table_id == table.id
                        ]
                    }
                )
                for table in tables
            ],
        )

    async def save_setup(
        self, datasource_id: int, data: SetupWrite, grants: Any, *, actor: CurrentUser
    ) -> SetupResult:
        """Save semantics and the table selection; publishing imports the selected tables.

        A semantic equal to the column's database comment is stored as "use the comment", so
        a later comment change still shows. Drafts never change what anyone can see, except
        that a description edited on an imported column is live at once (as on the schema
        page). Publishing refuses while any selected table has a column without a semantic.
        """
        require_admin(actor)
        await self.lock_catalog(datasource_id)
        tables, columns = await self.repository.setup_rows(datasource_id)
        by_table = {table.id: table for table in tables}
        by_column = {column.id: column for column in columns}
        include: dict[int, bool] = {}
        for item in data.tables:
            table = by_table.get(item.table_id)
            if table is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            include[table.id] = item.include
            if item.description is not None:
                text = _semantic(item.description)
                table.description = "" if text == _semantic(table.comment) else text
            for written in item.columns:
                column = by_column.get(written.column_id)
                if column is None or column.table_id != table.id:
                    raise AppError(ErrorCode.NOT_FOUND, http_status=404)
                text = _semantic(written.description)
                stored = "" if text == _semantic(column.comment) else text
                if stored != column.description:
                    column.description = stored
                    if column.status == "active" and table.status == "active":
                        await self.index.mark_dirty("column", column.id, datasource_id, actor=actor)
        for table_id, wanted in include.items():
            table = by_table[table_id]
            # Choosing among tables not imported yet changes nothing anyone sees.
            if table.status in {"pending", "excluded"}:
                table.status = "pending" if wanted else "excluded"
        result = SetupResult(published=data.publish)
        if data.publish:
            selected = [t for t in tables if include.get(t.id, t.status in {"active", "pending"})]
            missing = sum(
                1
                for column in columns
                if column.table_id in {t.id for t in selected}
                and not (_semantic(column.description) or _semantic(column.comment))
            )
            if missing:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR, f"还有 {missing} 个字段没有语义", http_status=422
                )
            dropped = [t for t in tables if t.status == "active" and not include.get(t.id, True)]
            imported, released = [], []
            for table in selected:
                if table.status != "active":
                    table.status = "active"
                    imported.append(table.id)
                for column in columns:
                    if column.table_id == table.id and column.status == "pending":
                        column.status = "active"
                        released.append(column.id)
            for table in dropped:
                table.status = "excluded"
            await self.repository.session.flush()
            for table_id in imported:
                await self.index.mark_dirty("table", table_id, datasource_id, actor=actor)
            for column in columns:
                if column.id in released or column.table_id in imported:
                    await self.index.mark_dirty("column", column.id, datasource_id, actor=actor)
            change = SyncResult(
                datasource_id=datasource_id,
                removed_tables=[t.id for t in dropped],
                removed_columns=[],
                table_count=len(selected),
                added_tables=imported,
                added_columns=[c.id for c in columns if c.id in released or c.table_id in imported],
            )
            # Imported objects inherit visibility like freshly synced ones; dropped ones lose it.
            await grants.expire_metadata(change)
            await grants.apply_inherited_defaults(change, actor=actor)
            result = SetupResult(
                published=True,
                imported_tables=len(imported),
                released_columns=len(released),
                excluded_tables=len(dropped),
            )
        await self.repository.session.flush()
        await self.audit.record(
            actor,
            "schema.setup.publish" if data.publish else "schema.setup.save",
            ("datasource", str(datasource_id)),
            result.model_dump(),
        )
        return result

    async def summaries(self, *, actor: CurrentUser) -> list[SetupSummary]:
        """What each datasource still needs from an administrator."""
        require_admin(actor)
        result = []
        for datasource_id, strict in (await self.datasource.semantics_flags()).items():
            tables, columns = await self.repository.setup_rows(datasource_id)
            imported = {t.id for t in tables if t.status == "active"}
            result.append(
                SetupSummary(
                    datasource_id=datasource_id,
                    require_semantics=strict,
                    tables=len(imported),
                    pending_tables=sum(t.status == "pending" for t in tables),
                    pending_columns=sum(
                        c.status == "pending" and c.table_id in imported for c in columns
                    ),
                    undocumented_columns=sum(
                        c.status == "active"
                        and c.table_id in imported
                        and not (_semantic(c.description) or _semantic(c.comment))
                        for c in columns
                    ),
                )
            )
        return result

    async def draft(
        self, datasource_id: int, data: DraftRequest, *, actor: CurrentUser
    ) -> DraftResult:
        """Model-proposed semantics for the given columns; the administrator reviews them."""
        require_admin(actor)
        if self.drafter is None:
            raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
        setup = await self.setup(datasource_id, actor=actor)
        wanted = set(data.column_ids)
        found: dict[int, str] = {}
        for table in setup.tables:
            asked = [c for c in table.columns if c.id in wanted]
            if asked:
                proposed = await self.drafter.draft(table, asked, actor=actor)
                found.update(
                    {cid: _semantic(text)[:200] for cid, text in proposed.items() if cid in wanted}
                )
        if wanted - {c.id for t in setup.tables for c in t.columns}:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.audit.record(
            actor,
            "schema.setup.draft",
            ("datasource", str(datasource_id)),
            {"asked": len(wanted), "proposed": len(found)},
        )
        return DraftResult(descriptions={k: v for k, v in found.items() if v})

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
    session: AsyncSession,
    box: SecretBox,
    inspector: DatasourceInspector | None = None,
    drafter: DescriptionDrafter | None = None,
) -> SchemaService:
    return SchemaService(
        SchemaRepository(session),
        build_datasource(session, box, inspector),
        audit_for(session),
        drafter,
    )


def get_service() -> SchemaService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


def get_permissions() -> PermissionService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


def get_grants() -> Any:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


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
                int(context.task.payload["datasource_id"]),
                grants,
                actor=actor,
                progress=context.progress,
            )
            await context.progress(90)
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
