from typing import Any, Protocol

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.core.scope import PromotionService as ScopePromotion
from app.core.scope import ScopedQuery, ScopeLevel, ScopeMixin
from app.modules.embedding.service import build_outbox
from app.modules.schema.service import SchemaService
from app.modules.semantic.repository import SemanticTermRepository
from app.modules.semantic.schemas import SemanticInput, SemanticView, TargetReference


class TargetResolver(Protocol):
    """Adapter to schema.service, never to schema repositories."""

    async def resolve_target(
        self, datasource_id: int, kind: str, ref_id: int
    ) -> TargetReference: ...


class IndexPublisher(Protocol):
    async def mark_dirty(
        self, kind: str, ref_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None: ...


def creation_scope(level: ScopeLevel, actor: CurrentUser) -> dict[str, Any]:
    allowed = {
        Role.ADMIN: ScopeLevel.PLATFORM,
        Role.LEADER: ScopeLevel.TEAM,
        Role.MEMBER: ScopeLevel.PERSONAL,
    }
    if not actor.active or allowed[actor.role] != level:
        raise PermissionDenied()
    return {
        "scope": level,
        "owner_team_id": actor.team_id if level != ScopeLevel.PLATFORM else None,
        "owner_user_id": actor.id if level == ScopeLevel.PERSONAL else None,
        "status": {
            ScopeLevel.PLATFORM: "active",
            ScopeLevel.TEAM: "team",
            ScopeLevel.PERSONAL: "private",
        }[level],
    }


def require_edit(item: ScopeMixin, actor: CurrentUser) -> None:
    if not actor.active or item.status == "pending":
        raise AppError(ErrorCode.CONFLICT)
    # Admins govern shared definitions: team-level items too, keeping their owner and scope.
    # Personal items stay with their owner.
    if actor.role == Role.ADMIN and item.scope == ScopeLevel.TEAM:
        return
    expected = creation_scope(item.scope, actor)
    if item.owner_team_id != expected["owner_team_id"] or (
        item.scope != ScopeLevel.TEAM and item.owner_user_id != expected["owner_user_id"]
    ):
        raise PermissionDenied()


class SemanticService:
    def __init__(
        self,
        repository: SemanticTermRepository,
        permissions: PermissionService,
        targets: TargetResolver,
        audit: AuditService,
        index: IndexPublisher,
    ) -> None:
        self.repository, self.permissions, self.targets = repository, permissions, targets
        self.audit, self.index = audit, index

    async def _authorize(self, data: SemanticInput, actor: CurrentUser) -> None:
        scope = await self.permissions.visible_scope(actor, data.datasource_id)
        target = await self.targets.resolve_target(
            data.datasource_id, data.target_type, data.target_id
        )
        table = scope.tables.get(target.table)
        if table is None or (
            data.target_type != "table"
            and (target.column is None or target.column not in table.columns)
        ):
            raise PermissionDenied()
        # A table-level description may reveal any field: require the whole table grant.
        if data.target_type == "table" and table.columns != frozenset(table.all_columns):
            raise PermissionDenied()

    async def list(self, actor: CurrentUser, datasource_id: int) -> list[SemanticView]:
        await self.permissions.visible_scope(actor, datasource_id)
        items = ScopedQuery.visible_to(
            [
                SemanticView.model_validate(row)
                for row in await self.repository.candidates(datasource_id)
            ],
            actor,
        )
        visible = []
        for item in items:
            try:
                await self._authorize(item, actor)
            except AppError as error:
                if error.code in {ErrorCode.FORBIDDEN, ErrorCode.NOT_FOUND}:
                    continue
                raise
            visible.append(item)
        return visible

    async def get(self, identifier: int, actor: CurrentUser) -> SemanticView:
        row = await self.repository.get(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        item = SemanticView.model_validate(row)
        if not ScopedQuery.visible_to([item], actor):
            raise PermissionDenied()
        await self._authorize(item, actor)
        return item

    async def save(
        self, data: SemanticInput, *, actor: CurrentUser, identifier: int | None = None
    ) -> SemanticView:
        async with self.repository.atomic():
            await self._authorize(data, actor)
            values = {
                **data.model_dump(),
                "target_table_id": data.target_id if data.target_type == "table" else None,
                "target_column_id": data.target_id if data.target_type != "table" else None,
            }
            before: dict[str, Any] = {}
            if identifier is None:
                values.update(creation_scope(data.scope, actor), version=1)
                row = await self.repository.create(values, actor.id)
            else:
                existing = await self.repository.locked(identifier)
                if existing is None:
                    raise AppError(ErrorCode.NOT_FOUND, http_status=404)
                old = SemanticView.model_validate(existing)
                require_edit(old, actor)
                if data.scope != old.scope or data.datasource_id != old.datasource_id:
                    raise AppError(ErrorCode.CONFLICT)
                before = old.model_dump(mode="json")
                row = await self.repository.update(existing, {**values, "version": old.version + 1})
            item = SemanticView.model_validate(row)
            await self.audit.record(
                actor,
                "semantic.save",
                ("semantic", str(item.id)),
                {"before": before, "after": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("semantic", item.id, item.datasource_id, actor=actor)
            return item

    async def delete(self, identifier: int, *, actor: CurrentUser) -> None:
        async with self.repository.atomic():
            row = await self.repository.locked(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            item = SemanticView.model_validate(row)
            require_edit(item, actor)
            await self._authorize(item, actor)
            await self.repository.soft_delete(row)
            await self.audit.record(
                actor,
                "semantic.delete",
                ("semantic", str(identifier)),
                {"before": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("semantic", identifier, item.datasource_id, actor=actor)

    async def promote(
        self, identifier: int, *, actor: CurrentUser, review: bool | None = None, reason: str = ""
    ) -> SemanticView:
        async with self.repository.atomic():
            row = await self.repository.locked(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            old = SemanticView.model_validate(row)
            changed = (
                ScopePromotion.request(old, actor)
                if review is None
                else ScopePromotion.review(old, actor, review, reason)
            )
            await self._authorize(old, actor)
            row = await self.repository.update(
                row,
                {
                    **changed.model_dump(
                        include={"scope", "status", "owner_team_id", "owner_user_id"}
                    ),
                    "version": old.version + 1,
                },
            )
            item = SemanticView.model_validate(row)
            await self.audit.record(
                actor,
                "semantic.promote",
                ("semantic", str(item.id)),
                {"before": old.model_dump(mode="json"), "after": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("semantic", item.id, item.datasource_id, actor=actor)
            return item


class SchemaTargets:
    """Resolve polymorphic targets through the schema service's catalog API."""

    def __init__(self, schema: "SchemaService") -> None:
        self.schema = schema

    async def resolve_target(self, datasource_id: int, kind: str, ref_id: int) -> TargetReference:
        for table in await self.schema.catalog(datasource_id):
            if kind == "table" and table.id == ref_id:
                return TargetReference(table=table.qualified_name)
            if kind in {"column", "enum"}:
                for column in table.columns:
                    if column.id == ref_id:
                        return TargetReference(table=table.qualified_name, column=column.name)
        raise AppError(ErrorCode.NOT_FOUND, http_status=404)


def build_service(
    session: "AsyncSession", permissions: PermissionService, targets: TargetResolver
) -> SemanticService:
    return SemanticService(
        SemanticTermRepository(session),
        permissions,
        targets,
        AuditService(DatabaseAuditStore(session)),
        build_outbox(session),
    )


async def index_catalog(service: SemanticService, datasource_id: int) -> list[tuple[int, int]]:
    """Internal worker metadata; public content reads still use get/PermissionService."""
    return [
        (row.id, owner)
        for row in await service.repository.candidates(datasource_id)
        if (owner := row.owner_user_id or row.created_by) is not None
    ]
