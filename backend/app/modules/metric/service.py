import math
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.datasource import Datasource
from app.core.db import AsyncSession
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.core.scope import PromotionService as ScopePromotion
from app.core.scope import ScopedQuery
from app.core.sql.executor import QueryLimits, QueryResult, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.modules.datasource.service import DatasourceService
from app.modules.embedding.service import build_outbox
from app.modules.metric.repository import MetricRepository
from app.modules.metric.schemas import MetricInput, MetricView
from app.modules.semantic.service import IndexPublisher, creation_scope, require_edit


class DatasourceResolver(Protocol):
    async def resolve(self, datasource_id: int, actor: CurrentUser) -> Datasource: ...


_PARAMETER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def render_template(template: str, params: dict[str, str | int | float | bool | None]) -> str:
    if set(_PARAMETER.findall(template)) != set(params):
        raise AppError(ErrorCode.VALIDATION_ERROR)

    def literal(match: re.Match[str]) -> str:
        value = params[match.group(1)]
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            return str(value)
        # No backslash escapes or NUL: PostgreSQL and MySQL must interpret it identically.
        if "\\" in value or "\x00" in value:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        return "'" + value.replace("'", "''") + "'"

    return _PARAMETER.sub(literal, template)


class MetricService:
    def __init__(
        self,
        repository: MetricRepository,
        permissions: PermissionService,
        datasources: DatasourceResolver,
        guard: SqlGuard,
        executor: SqlExecutor,
        audit: AuditService,
        index: IndexPublisher,
    ) -> None:
        self.repository, self.permissions, self.datasources = repository, permissions, datasources
        self.guard, self.executor, self.audit, self.index = guard, executor, audit, index

    async def _authorize(self, data: MetricInput, actor: CurrentUser) -> None:
        scope = await self.permissions.visible_scope(actor, data.datasource_id)
        datasource = await self.datasources.resolve(data.datasource_id, actor)
        self.guard.check(render_template(data.sql_template, data.params), datasource.dialect, scope)

    async def trial(self, data: MetricInput, *, actor: CurrentUser) -> QueryResult:
        scope = await self.permissions.visible_scope(actor, data.datasource_id)
        datasource = await self.datasources.resolve(data.datasource_id, actor)
        checked = self.guard.check(
            render_template(data.sql_template, data.params), datasource.dialect, scope
        )
        # Guard the bounded SELECT again so its execution capability covers the exact SQL.
        bounded = self.guard.check(
            f"SELECT * FROM ({checked.normalized_sql}) AS metric_trial LIMIT 100",
            datasource.dialect,
            scope,
        )
        return await self.executor.run(
            datasource, bounded, QueryLimits(user_id=actor.id, max_rows=100)
        )

    async def list(self, actor: CurrentUser, datasource_id: int) -> list[MetricView]:
        await self.permissions.visible_scope(actor, datasource_id)
        items = ScopedQuery.visible_to(
            [
                MetricView.model_validate(row)
                for row in await self.repository.candidates(datasource_id)
            ],
            actor,
        )
        visible = []
        for item in items:
            try:
                await self._authorize(item, actor)
            except AppError as error:
                if error.code in {
                    ErrorCode.FORBIDDEN,
                    ErrorCode.NOT_FOUND,
                    ErrorCode.SQL_OUT_OF_SCOPE,
                }:
                    continue
                raise
            visible.append(item)
        return visible

    async def get(self, identifier: int, actor: CurrentUser) -> MetricView:
        row = await self.repository.get(identifier)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        item = MetricView.model_validate(row)
        if not ScopedQuery.visible_to([item], actor):
            raise PermissionDenied()
        await self._authorize(item, actor)
        return item

    async def save(
        self, data: MetricInput, *, actor: CurrentUser, identifier: int | None = None
    ) -> MetricView:
        async with self.repository.atomic():
            existing = await self.repository.locked(identifier) if identifier is not None else None
            before: dict[str, Any] = {}
            if identifier is None:
                owners = creation_scope(data.scope, actor)
            else:
                if existing is None:
                    raise AppError(ErrorCode.NOT_FOUND, http_status=404)
                old = MetricView.model_validate(existing)
                require_edit(old, actor)
                if data.scope != old.scope or data.datasource_id != old.datasource_id:
                    raise AppError(ErrorCode.CONFLICT)
                before = old.model_dump(mode="json")
                owners = {}
            await self.trial(data, actor=actor)
            values = {**data.model_dump(), "last_validated_at": datetime.now(UTC)}
            if existing is None:
                row = await self.repository.create({**values, **owners, "version": 1}, actor.id)
            else:
                row = await self.repository.update(
                    existing, {**values, "version": existing.version + 1}
                )
            item = MetricView.model_validate(row)
            await self.audit.record(
                actor,
                "metric.save",
                ("metric", str(item.id)),
                {"before": before, "after": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("metric", item.id, item.datasource_id, actor=actor)
            return item

    async def delete(self, identifier: int, *, actor: CurrentUser) -> None:
        async with self.repository.atomic():
            row = await self.repository.locked(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            item = MetricView.model_validate(row)
            require_edit(item, actor)
            await self._authorize(item, actor)
            await self.repository.soft_delete(row)
            await self.audit.record(
                actor,
                "metric.delete",
                ("metric", str(identifier)),
                {"before": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("metric", identifier, item.datasource_id, actor=actor)

    async def promote(
        self, identifier: int, *, actor: CurrentUser, review: bool | None = None, reason: str = ""
    ) -> MetricView:
        async with self.repository.atomic():
            row = await self.repository.locked(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            old = MetricView.model_validate(row)
            changed = (
                ScopePromotion.request(old, actor)
                if review is None
                else ScopePromotion.review(old, actor, review, reason)
            )
            await self._authorize(old, actor)
            if review is True:
                await self.trial(old, actor=actor)
            row = await self.repository.update(
                row,
                {
                    **changed.model_dump(
                        include={"scope", "status", "owner_team_id", "owner_user_id"}
                    ),
                    "version": old.version + 1,
                },
            )
            item = MetricView.model_validate(row)
            await self.audit.record(
                actor,
                "metric.promote",
                ("metric", str(item.id)),
                {"before": old.model_dump(mode="json"), "after": item.model_dump(mode="json")},
            )
            await self.index.mark_dirty("metric", item.id, item.datasource_id, actor=actor)
            return item


class QueryDatasources:
    def __init__(self, datasource: "DatasourceService", permissions: PermissionService) -> None:
        self.datasource, self.permissions = datasource, permissions

    async def resolve(self, datasource_id: int, actor: CurrentUser) -> Datasource:
        return await self.datasource.for_query(datasource_id, actor, self.permissions)


def build_service(
    session: "AsyncSession",
    permissions: PermissionService,
    datasources: DatasourceResolver,
    executor: SqlExecutor,
) -> MetricService:
    return MetricService(
        MetricRepository(session),
        permissions,
        datasources,
        SqlGuard(),
        executor,
        AuditService(DatabaseAuditStore(session)),
        build_outbox(session),
    )


async def index_catalog(service: MetricService, datasource_id: int) -> list[tuple[int, int]]:
    """Internal worker metadata; public content reads still use get/PermissionService."""
    return [
        (row.id, owner)
        for row in await service.repository.candidates(datasource_id)
        if (owner := row.owner_user_id or row.created_by) is not None
    ]
