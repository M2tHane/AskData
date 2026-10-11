from typing import Annotated

from fastapi import Depends, Request

from app.core.audit import AuditService
from app.core.crypto import SecretBox
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.masking import MaskRule
from app.core.permission import (
    PermissionService,
    ScopeCache,
    TableGrant,
    VisibleScope,
)
from app.modules.auth.service import audit_for, get_session, require_admin, secret_box, settings_for
from app.modules.org.service import OrgService
from app.modules.org.service import build_service as build_org
from app.modules.permission.repository import PermissionRepository
from app.modules.permission.schemas import (
    ColumnGrantWrite,
    ColumnVisibility,
    DatasourceVisibilityWrite,
    FullVisibilityWrite,
    GrantRead,
    MemberGrantsWrite,
    ObjectVisibilityWrite,
    PublicScope,
    PublicTableScope,
    TableGrantWrite,
    TableVisibility,
    TeamGrantsWrite,
    VisibilityRead,
)
from app.modules.permission.visibility import (
    ColumnNode,
    TableNode,
    derive_overrides,
    resolve,
)
from app.modules.schema.service import SchemaService, SyncResult, TableRead
from app.modules.schema.service import build_service as build_schema


class SessionScopeCache:
    """Publish snapshots only after commit, so rolled-back grants cannot escape via Redis."""

    def __init__(self, session: AsyncSession, cache: ScopeCache) -> None:
        self.session, self.cache = session, cache

    async def revision(self, datasource_id: int) -> str:
        return await self.cache.revision(datasource_id)

    async def get(
        self, user: CurrentUser, datasource_id: int, revision: str
    ) -> VisibleScope | None:
        return await self.cache.get(user, datasource_id, revision)

    async def put(self, user: CurrentUser, scope: VisibleScope, revision: str) -> None:
        snapshot = scope.model_copy(deep=True)

        async def publish() -> None:
            await self.cache.put(user, snapshot, revision)

        self.session.info.setdefault("after_commit", []).append(publish)

    async def invalidate(self, datasource_id: int) -> None:
        await self.cache.invalidate(datasource_id)


class GrantService:
    def __init__(
        self,
        repository: PermissionRepository,
        schema: SchemaService,
        org: OrgService,
        audit: AuditService,
        cache: ScopeCache | None = None,
    ) -> None:
        self.repository, self.schema, self.org, self.audit = repository, schema, org, audit
        self.permissions = PermissionService(
            self, SessionScopeCache(repository.session, cache) if cache else None
        )

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]:
        await self.schema.datasource.connection(datasource_id)
        catalog = await self.schema.catalog(datasource_id)
        tables, columns = await self.repository.grants(
            team_id, [t.id for t in catalog], [c.id for t in catalog for c in t.columns]
        )
        by_table = {t.table_id: t for t in tables}
        by_column = {c.column_id: c for c in columns}
        result = []
        for table in catalog:
            grant = by_table.get(table.id)
            cols = [c for c in table.columns if c.id in by_column]
            result.append(
                TableGrant(
                    name=table.qualified_name,
                    all_columns={c.name: c.data_type for c in table.columns},
                    team_visible=grant is not None,
                    member_visible=bool(grant and grant.member_visible),
                    team_columns=frozenset(c.name for c in cols),
                    member_columns=frozenset(
                        c.name for c in cols if by_column[c.id].member_visible
                    ),
                    masks={
                        c.name: MaskRule.model_validate(by_column[c.id].mask_rule)
                        for c in cols
                        if by_column[c.id].mask_rule
                    },
                )
            )
        return result

    def _manage(self, team_id: int, actor: CurrentUser) -> None:
        if not actor.active or (
            actor.role != Role.ADMIN and (actor.role != Role.LEADER or actor.team_id != team_id)
        ):
            raise PermissionDenied()

    async def read(self, datasource_id: int, team_id: int, *, actor: CurrentUser) -> GrantRead:
        self._manage(team_id, actor)
        catalog = await self.schema.catalog(datasource_id)
        tables, columns = await self.repository.grants(
            team_id, [t.id for t in catalog], [c.id for t in catalog for c in t.columns]
        )
        by_table = {t.table_id: t for t in tables}
        by_column = {c.column_id: c for c in columns}
        return GrantRead(
            datasource_id=datasource_id,
            team_id=team_id,
            tables=[
                TableGrantWrite(
                    table_id=t.id,
                    member_visible=by_table[t.id].member_visible,
                    columns=[
                        ColumnGrantWrite(
                            column_id=c.id,
                            member_visible=by_column[c.id].member_visible,
                            mask_rule=MaskRule.model_validate(by_column[c.id].mask_rule)
                            if by_column[c.id].mask_rule
                            else None,
                        )
                        for c in t.columns
                        if c.id in by_column
                    ],
                )
                for t in catalog
                if t.id in by_table
            ],
        )

    async def public_scope(self, datasource_id: int, *, actor: CurrentUser) -> PublicScope:
        scope = await self.permissions.visible_scope(actor, datasource_id)
        return PublicScope(
            datasource_id=datasource_id,
            tables={
                name: PublicTableScope(columns=sorted(table.columns), masks=table.masks)
                for name, table in scope.tables.items()
            },
        )

    @staticmethod
    def _ids(catalog: list[TableRead]) -> tuple[list[int], list[int]]:
        return [t.id for t in catalog], [c.id for t in catalog for c in t.columns]

    async def replace(
        self, datasource_id: int, team_id: int, data: TeamGrantsWrite, *, actor: CurrentUser
    ) -> GrantRead:
        require_admin(actor)
        await self.org.ensure_team(team_id)
        await self.schema.lock_catalog(datasource_id)
        catalog = await self.schema.catalog(datasource_id)
        by_table = {t.id: t for t in catalog}
        seen_tables: set[int] = set()
        seen_columns: set[int] = set()
        for table in data.tables:
            if table.table_id not in by_table or table.table_id in seen_tables:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            seen_tables.add(table.table_id)
            columns = {c.id: c for c in by_table[table.table_id].columns}
            for column in table.columns:
                if column.column_id not in columns or column.column_id in seen_columns:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                if column.member_visible and not table.member_visible:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                seen_columns.add(column.column_id)
        table_ids, column_ids = self._ids(catalog)
        await self.repository.replace(team_id, table_ids, column_ids, data, actor.id)
        await self._normalize(datasource_id, actor)
        await self._invalidate(datasource_id)
        await self.audit.record(
            actor,
            "permission.replace",
            ("datasource", str(datasource_id)),
            {"team_id": team_id, "table_count": len(data.tables)},
        )
        return await self.read(datasource_id, team_id, actor=actor)

    async def set_members(
        self, datasource_id: int, team_id: int, data: MemberGrantsWrite, *, actor: CurrentUser
    ) -> GrantRead:
        self._manage(team_id, actor)
        await self.org.ensure_team(team_id)
        await self.schema.lock_catalog(datasource_id)
        # Ask core for the team's upper bound, independent of an admin's broader scope.
        leader = CurrentUser(id=actor.id, role=Role.LEADER, team_id=team_id)
        scope = await PermissionService(self).visible_scope(leader, datasource_id)
        catalog = await self.schema.catalog(datasource_id)
        by_table = {t.id: t for t in catalog}
        selected_tables: set[int] = set()
        selected_columns: set[int] = set()
        for table in data.tables:
            meta = by_table.get(table.table_id)
            visible = scope.tables.get(meta.qualified_name) if meta else None
            if meta is None or visible is None or table.table_id in selected_tables:
                raise PermissionDenied()
            selected_tables.add(table.table_id)
            columns = {c.id: c for c in meta.columns}
            for identifier in table.column_ids:
                if identifier not in columns or columns[identifier].name not in visible.columns:
                    raise PermissionDenied()
                if identifier in selected_columns:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                selected_columns.add(identifier)
        table_ids, column_ids = self._ids(catalog)
        await self.repository.set_members(
            team_id, table_ids, column_ids, selected_tables, selected_columns
        )
        await self._invalidate(datasource_id)
        await self.audit.record(
            actor, "permission.members", ("datasource", str(datasource_id)), {"team_id": team_id}
        )
        return await self.read(datasource_id, team_id, actor=actor)

    async def grant_upload_owner(
        self, datasource_id: int, team_id: int, *, actor: CurrentUser
    ) -> None:
        owner = await self.schema.datasource.upload_owner(datasource_id)
        self.schema.datasource.require_upload_owner(owner, actor)
        if owner != team_id:
            raise PermissionDenied()
        await self.schema.lock_catalog(datasource_id)
        if not await self.schema.datasource.upload_grants_pending(datasource_id, actor=actor):
            raise AppError(ErrorCode.CONFLICT)
        await self.org.ensure_team(team_id)
        tables = await self.schema.catalog(datasource_id)
        await self.repository.initialize_owner(
            team_id,
            [(table.id, [column.id for column in table.columns]) for table in tables],
            actor.id,
        )
        await self._normalize(datasource_id, actor)
        await self._invalidate(datasource_id)
        await self.audit.record(
            actor,
            "permission.upload.owner",
            ("datasource", str(datasource_id)),
            {"team_id": team_id},
        )

    async def _load(self, datasource_id: int) -> tuple[set[int], list[TableNode]]:
        catalog = await self.schema.catalog(datasource_id)
        table_ids, column_ids = self._ids(catalog)
        teams = await self.repository.datasource_teams(datasource_id)
        custom_tables, custom_columns = await self.repository.overrides(table_ids, column_ids)
        table_sets, column_sets = await self.repository.team_sets(table_ids, column_ids)
        nodes = [
            TableNode(
                id=table.id,
                custom=table.id in custom_tables,
                teams=table_sets.get(table.id, set()),
                columns=[
                    ColumnNode(
                        id=column.id,
                        custom=column.id in custom_columns,
                        teams=column_sets.get(column.id, set()),
                    )
                    for column in table.columns
                ],
            )
            for table in catalog
        ]
        return teams, nodes

    async def _store(
        self, datasource_id: int, teams: set[int], nodes: list[TableNode], actor: CurrentUser
    ) -> int:
        resolved = resolve(teams, nodes)
        created = await self.repository.apply_sets(resolved.tables, resolved.columns, actor.id)
        await self.repository.set_datasource_teams(datasource_id, teams, actor.id)
        await self.repository.store_overrides(
            [n.id for n in nodes],
            [c.id for n in nodes for c in n.columns],
            {n.id for n in nodes if n.custom},
            {c.id for n in nodes for c in n.columns if c.custom},
            actor.id,
        )
        await self._invalidate(datasource_id)
        return created

    @staticmethod
    def _read_visibility(
        datasource_id: int, teams: set[int], nodes: list[TableNode]
    ) -> VisibilityRead:
        resolved = resolve(teams, nodes)
        return VisibilityRead(
            datasource_id=datasource_id,
            team_ids=sorted(teams),
            custom=any(n.custom or any(c.custom for c in n.columns) for n in nodes),
            tables=[
                TableVisibility(
                    table_id=n.id,
                    custom=n.custom,
                    team_ids=sorted(resolved.tables[n.id]),
                    columns=[
                        ColumnVisibility(
                            column_id=c.id,
                            custom=c.custom,
                            team_ids=sorted(resolved.columns[c.id]),
                        )
                        for c in n.columns
                    ],
                )
                for n in nodes
            ],
        )

    async def visibility(self, datasource_id: int, *, actor: CurrentUser) -> VisibilityRead:
        require_admin(actor)
        await self.schema.datasource.require_managed(datasource_id)
        teams, nodes = await self._load(datasource_id)
        resolved = resolve(teams, nodes)
        for node in nodes:
            node.teams = resolved.tables[node.id]
            for column in node.columns:
                column.teams = resolved.columns[column.id]
        return self._read_visibility(datasource_id, teams, nodes)

    async def _team_ids(self, team_ids: list[int]) -> set[int]:
        unique = set(team_ids)
        for team_id in unique:
            await self.org.ensure_team(team_id)
        return unique

    async def _audit_visibility(
        self, actor: CurrentUser, datasource_id: int, kind: str, **detail: int
    ) -> None:
        await self.audit.record(
            actor,
            f"permission.visibility.{kind}",
            ("datasource", str(datasource_id)),
            detail,
        )

    async def set_datasource_scope(
        self, datasource_id: int, data: DatasourceVisibilityWrite, *, actor: CurrentUser
    ) -> VisibilityRead:
        require_admin(actor)
        await self.schema.lock_catalog(datasource_id)
        wanted = await self._team_ids(data.team_ids)
        _, nodes = await self._load(datasource_id)
        await self._store(datasource_id, wanted, nodes, actor)
        await self._audit_visibility(actor, datasource_id, "datasource", teams=len(wanted))
        return await self.visibility(datasource_id, actor=actor)

    async def set_table_scope(
        self,
        datasource_id: int,
        table_id: int,
        data: ObjectVisibilityWrite,
        *,
        actor: CurrentUser,
    ) -> VisibilityRead:
        require_admin(actor)
        await self.schema.lock_catalog(datasource_id)
        teams, nodes = await self._load(datasource_id)
        node = next((n for n in nodes if n.id == table_id), None)
        if node is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if data.inherit:
            node.custom = False
            for column in node.columns:
                column.custom = False
        else:
            wanted = await self._team_ids(data.team_ids)
            if not wanted <= teams:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            node.custom, node.teams = True, wanted
        await self._store(datasource_id, teams, nodes, actor)
        await self._audit_visibility(actor, datasource_id, "table", table_id=table_id)
        return await self.visibility(datasource_id, actor=actor)

    async def set_column_scope(
        self,
        datasource_id: int,
        column_id: int,
        data: ObjectVisibilityWrite,
        *,
        actor: CurrentUser,
    ) -> VisibilityRead:
        require_admin(actor)
        await self.schema.lock_catalog(datasource_id)
        teams, nodes = await self._load(datasource_id)
        located = next(((n, c) for n in nodes for c in n.columns if c.id == column_id), None)
        if located is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        table, column = located
        if data.inherit:
            column.custom = False
        else:
            wanted = await self._team_ids(data.team_ids)
            if not wanted <= resolve(teams, nodes).tables[table.id]:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            column.custom, column.teams = True, wanted
        await self._store(datasource_id, teams, nodes, actor)
        await self._audit_visibility(actor, datasource_id, "column", column_id=column_id)
        return await self.visibility(datasource_id, actor=actor)

    async def set_visibility_full(
        self, datasource_id: int, data: FullVisibilityWrite, *, actor: CurrentUser
    ) -> VisibilityRead:
        require_admin(actor)
        await self.schema.lock_catalog(datasource_id)
        wanted = await self._team_ids(data.team_ids)
        _, nodes = await self._load(datasource_id)
        for node in nodes:
            node.custom = False
            for column in node.columns:
                column.custom = False
        by_table = {n.id: n for n in nodes}
        seen_tables: set[int] = set()
        for entry in data.tables:
            found = by_table.get(entry.table_id)
            if found is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            if entry.table_id in seen_tables:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            seen_tables.add(entry.table_id)
            node_ = found
            table_teams = set(wanted)
            if entry.custom:
                table_teams = await self._team_ids(entry.team_ids)
                if not table_teams <= wanted:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                node_.custom, node_.teams = True, table_teams
            by_column = {c.id: c for c in node_.columns}
            seen_columns: set[int] = set()
            for item in entry.columns:
                target = by_column.get(item.column_id)
                if target is None:
                    raise AppError(ErrorCode.NOT_FOUND, http_status=404)
                if item.column_id in seen_columns:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                seen_columns.add(item.column_id)
                column_teams = await self._team_ids(item.team_ids)
                if not column_teams <= table_teams:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                target.custom, target.teams = True, column_teams
        await self._store(datasource_id, wanted, nodes, actor)
        await self._audit_visibility(
            actor, datasource_id, "full", teams=len(wanted), tables=len(data.tables)
        )
        return await self.visibility(datasource_id, actor=actor)

    async def reset_visibility(self, datasource_id: int, *, actor: CurrentUser) -> VisibilityRead:
        require_admin(actor)
        await self.schema.lock_catalog(datasource_id)
        teams, nodes = await self._load(datasource_id)
        for node in nodes:
            node.custom = False
            for column in node.columns:
                column.custom = False
        await self._store(datasource_id, teams, nodes, actor)
        await self._audit_visibility(actor, datasource_id, "reset")
        return await self.visibility(datasource_id, actor=actor)

    async def _normalize(self, datasource_id: int, actor: CurrentUser) -> None:
        """Re-derive scope and overrides after grants were written directly."""
        stored, nodes = await self._load(datasource_id)
        teams = stored | {t for n in nodes for t in n.teams}
        derive_overrides(teams, nodes)
        await self.repository.set_datasource_teams(datasource_id, teams, actor.id)
        await self.repository.store_overrides(
            [n.id for n in nodes],
            [c.id for n in nodes for c in n.columns],
            {n.id for n in nodes if n.custom},
            {c.id for n in nodes for c in n.columns if c.custom},
            actor.id,
        )

    async def apply_inherited_defaults(self, result: SyncResult, *, actor: CurrentUser) -> int:
        """Grant new tables and columns the visibility they inherit; never widen old ones.

        Inheriting objects already equal their parent's set, so reconciling is a no-op for
        everything that existed before this synchronization.
        """
        if not result.added_tables and not result.added_columns:
            return 0
        teams, nodes = await self._load(result.datasource_id)
        created = await self._store(result.datasource_id, teams, nodes, actor)
        await self.audit.record(
            actor,
            "permission.sync.default",
            ("datasource", str(result.datasource_id)),
            {
                "tables": len(result.added_tables),
                "columns": len(result.added_columns),
                "grants": created,
            },
        )
        return created

    async def _invalidate(self, datasource_id: int) -> None:
        await self.permissions.invalidate(datasource_id)

        async def committed() -> None:
            await self.permissions.invalidate(datasource_id)

        self.repository.session.info.setdefault("after_commit", []).append(committed)

    async def expire_metadata(self, result: SyncResult) -> None:
        await self.repository.expire(result.removed_tables, result.removed_columns)
        await self._invalidate(result.datasource_id)

    async def visible_scope(self, actor: CurrentUser, datasource_id: int) -> VisibleScope:
        return await self.permissions.visible_scope(actor, datasource_id)


def build_service(
    session: AsyncSession, box: SecretBox, cache: ScopeCache | None = None
) -> GrantService:
    return GrantService(
        PermissionRepository(session),
        build_schema(session, box),
        build_org(session),
        audit_for(session),
        cache,
    )


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session, scope="function")]
) -> GrantService:
    return build_service(
        session, secret_box(settings_for(request)), getattr(request.app.state, "scope_cache", None)
    )
