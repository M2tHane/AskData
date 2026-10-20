from pydantic import SecretStr

from app.core.audit import AuditService
from app.core.crypto import SecretBox
from app.core.datasource import ConnectionTest, Datasource, DatasourceInspector
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, DatasourceUnavailable, ErrorCode, PermissionDenied
from app.core.permission import PermissionService, ScopeCache
from app.core.response import Page, PageParams
from app.core.sql.dialect import Dialect
from app.modules.auth.service import (
    audit_for,
    require_admin,
)
from app.modules.datasource.repository import DatasourceRepository
from app.modules.datasource.schemas import DatasourceChoice, DatasourceRead, DatasourceWrite
from app.modules.datasource.schemas import UploadPublication as UploadPublication


class DatasourceService:
    def __init__(
        self,
        repository: DatasourceRepository,
        audit: AuditService,
        box: SecretBox,
        inspector: DatasourceInspector,
        cache: ScopeCache | None = None,
    ) -> None:
        self.repository, self.audit, self.box, self.inspector = repository, audit, box, inspector
        self.cache = cache

    async def lock(self, datasource_id: int) -> None:
        await self.repository.lock(datasource_id)

    async def _invalidate(self, datasource_id: int) -> None:
        if self.cache:
            await self.cache.invalidate(datasource_id)
            cache = self.cache

            async def committed() -> None:
                await cache.invalidate(datasource_id)

            self.repository.session.info.setdefault("after_commit", []).append(committed)

    async def require_managed(self, datasource_id: int) -> None:
        """Admin metadata management works on disabled sources too; only queries need active."""
        if await self.repository.get(datasource_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)

    async def connection(self, datasource_id: int) -> Datasource:
        row = await self.repository.get(datasource_id)
        if row is None or row.status != "active":
            raise DatasourceUnavailable()
        return Datasource(
            id=row.id,
            dialect=Dialect(row.dialect),
            host=row.host,
            port=row.port,
            database=row.database,
            username=row.username,
            password=self.box.decrypt(row.secret_enc),
            schema_name=row.schema_name,
            tls=row.tls,
            read_only=row.read_only_account,
        )

    async def for_query(
        self, datasource_id: int, actor: CurrentUser, permissions: PermissionService
    ) -> Datasource:
        scope = await permissions.visible_scope(actor, datasource_id)
        if not scope.tables:
            raise AppError(ErrorCode.FORBIDDEN, http_status=403)
        return await self.connection(datasource_id)

    async def listing(self, page: PageParams, *, actor: CurrentUser) -> Page[DatasourceRead]:
        require_admin(actor)
        rows = await self.repository.list(page.offset, page.page_size)
        return Page(
            items=[DatasourceRead.model_validate(row) for row in rows],
            total=await self.repository.count(),
            page=page.page,
            page_size=page.page_size,
        )

    async def choices(
        self, page: PageParams, permissions: PermissionService, *, actor: CurrentUser
    ) -> Page[DatasourceChoice]:
        choices = []
        for row in await self.repository.all_active():
            if (await permissions.visible_scope(actor, row.id)).tables:
                choices.append(
                    DatasourceChoice(id=row.id, name=row.name, type=row.type, dialect=row.dialect)
                )
        return Page(
            items=choices[page.offset : page.offset + page.page_size],
            total=len(choices),
            page=page.page,
            page_size=page.page_size,
        )

    async def requires_semantics(self, datasource_id: int) -> bool:
        row = await self.repository.get(datasource_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return bool(row.require_semantics)

    async def semantics_flags(self) -> dict[int, bool]:
        """Every database datasource and whether it requires semantics, for admin summaries.

        Uploads are left out: their columns come from file headers, not database comments.
        """
        return {
            row.id: bool(row.require_semantics)
            for row in await self.repository.managed()
            if row.type != "upload"
        }

    async def active_ids(self) -> list[int]:
        return await self.repository.active_ids()

    async def test_draft(self, data: DatasourceWrite, *, actor: CurrentUser) -> ConnectionTest:
        require_admin(actor)
        result = await self.inspector.test(self._draft(data))
        await self.audit.record(
            actor, "datasource.test", ("datasource", "draft"), result.model_dump()
        )
        return result

    @staticmethod
    def _draft(data: DatasourceWrite) -> Datasource:
        return Datasource(
            id=0,
            dialect=Dialect.from_name(data.type),
            host=data.host,
            port=data.port,
            database=data.database,
            username=data.username,
            password=data.password,
            schema_name=data.schema_name,
            tls=data.tls,
        )

    async def create(self, data: DatasourceWrite, *, actor: CurrentUser) -> DatasourceRead:
        require_admin(actor)
        result = await self.inspector.test(self._draft(data))
        if not result.available:
            raise DatasourceUnavailable()
        values = data.model_dump(exclude={"password"})
        values.update(
            secret_enc=self.box.encrypt(data.password),
            dialect=Dialect.from_name(data.type).value,
            read_only_account=result.read_only_account,
            status="active",
        )
        row = await self.repository.create(values, actor.id)
        await self.audit.record(
            actor, "datasource.create", ("datasource", str(row.id)), result.model_dump()
        )
        return DatasourceRead.model_validate(row).model_copy(update={"warning": result.warning})

    async def update(
        self, target_id: int, data: DatasourceWrite, *, actor: CurrentUser
    ) -> DatasourceRead:
        require_admin(actor)
        row = await self.repository.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        # Repointing an authorized datasource could expose an unrelated database.
        if any(
            getattr(row, key) != getattr(data, key)
            for key in ("type", "host", "port", "database", "schema_name")
        ):
            raise AppError(ErrorCode.CONFLICT)
        result = await self.inspector.test(self._draft(data))
        if not result.available:
            raise DatasourceUnavailable()
        # How a datasource was set up is fixed at creation.
        values = data.model_dump(exclude={"password", "require_semantics"})
        values.update(
            secret_enc=self.box.encrypt(data.password), read_only_account=result.read_only_account
        )
        await self.repository.update(row, values)
        await self.audit.record(
            actor, "datasource.update", ("datasource", str(row.id)), result.model_dump()
        )
        return DatasourceRead.model_validate(row).model_copy(update={"warning": result.warning})

    async def test(self, target_id: int, *, actor: CurrentUser) -> ConnectionTest:
        require_admin(actor)
        result = await self.inspector.test(await self.connection(target_id))
        row = await self.repository.get(target_id)
        if row is None:
            raise DatasourceUnavailable()
        await self.repository.update(
            row, {"read_only_account": result.available and result.read_only_account}
        )
        await self.audit.record(
            actor, "datasource.test", ("datasource", str(target_id)), result.model_dump()
        )
        return result

    @staticmethod
    def require_upload_owner(team_id: int | None, actor: CurrentUser) -> None:
        if not actor.active or (
            actor.role != Role.ADMIN
            and (team_id is None or actor.role != Role.LEADER or actor.team_id != team_id)
        ):
            raise PermissionDenied()

    async def reserve_upload(self, name: str, team_id: int | None, *, actor: CurrentUser) -> int:
        from app.modules.org.service import build_service as build_org

        self.require_upload_owner(team_id, actor)
        if not name.strip() or len(name) > 100:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if team_id is not None:
            await build_org(self.repository.session).ensure_team(team_id)
        row = await self.repository.create(
            {
                "name": name,
                "type": "upload",
                "host": "",
                "port": 5432,
                "database": "",
                "username": "",
                "secret_enc": self.box.encrypt(SecretStr("")),
                "schema_name": "pending",
                "dialect": Dialect.POSTGRES.value,
                "status": "pending",
                "owner_team_id": team_id,
                "read_only_account": False,
            },
            actor.id,
        )
        await self.repository.update(row, {"schema_name": f"ds_{row.id}"})
        await self.audit.record(actor, "datasource.upload.reserve", ("datasource", str(row.id)))
        return row.id

    async def upload_owner(self, datasource_id: int) -> int | None:
        row = await self.repository.get(datasource_id)
        if row is None or row.type != "upload":
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row.owner_team_id

    async def upload_grants_pending(self, datasource_id: int, *, actor: CurrentUser) -> bool:
        row = await self.repository.locked(datasource_id)
        team_id = await self.upload_owner(datasource_id)
        self.require_upload_owner(team_id, actor)
        return (
            row is not None
            and not row.upload_initialized
            and row.status == "active"
            and "pending" in row.upload_publications.values()
        )

    async def publish_upload_credentials(
        self, job_id: int, datasource: Datasource, owner_team_id: int | None, *, actor: CurrentUser
    ) -> UploadPublication:
        self.require_upload_owner(owner_team_id, actor)
        row = await self.repository.locked(datasource.id)
        if row is None or row.type != "upload" or row.owner_team_id != owner_team_id:
            raise PermissionDenied()
        if (
            job_id < 1
            or not datasource.read_only
            or datasource.dialect != Dialect.POSTGRES
            or datasource.schema_name != f"ds_{row.id}"
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        receipt = row.upload_publications.get(str(job_id))
        if receipt == "completed":
            return UploadPublication(
                datasource_id=row.id, owner_team_id=owner_team_id, completed=True
            )
        if row.status not in {"pending", "active"}:
            raise AppError(ErrorCode.CONFLICT)
        # Once bound, an update must not repoint grants to another database.
        if row.upload_initialized and any(
            getattr(row, field) != getattr(datasource, field)
            for field in ("host", "port", "database", "schema_name")
        ):
            raise AppError(ErrorCode.CONFLICT)
        await self.repository.update(
            row,
            {
                "host": datasource.host,
                "port": datasource.port,
                "database": datasource.database,
                "username": datasource.username,
                "secret_enc": self.box.encrypt(datasource.password),
                "schema_name": datasource.schema_name,
                "tls": datasource.tls,
                "read_only_account": True,
                "status": "active",
                "upload_publications": {**row.upload_publications, str(job_id): "pending"},
            },
        )
        await self._invalidate(row.id)
        await self.audit.record(
            actor, "datasource.upload.publish", ("datasource", str(row.id)), {"job_id": job_id}
        )
        return UploadPublication(
            datasource_id=row.id,
            owner_team_id=owner_team_id,
            initialize_owner_grants=not row.upload_initialized,
        )

    async def finish_upload_publication(
        self, job_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None:
        row = await self.repository.locked(datasource_id)
        team_id = await self.upload_owner(datasource_id)
        self.require_upload_owner(team_id, actor)
        if row is None or str(job_id) not in row.upload_publications:
            raise AppError(ErrorCode.CONFLICT)
        await self.repository.update(
            row,
            {
                "upload_initialized": True,
                "upload_publications": {**row.upload_publications, str(job_id): "completed"},
            },
        )
        await self.audit.record(
            actor,
            "datasource.upload.finish",
            ("datasource", str(datasource_id)),
            {"job_id": job_id},
        )

    async def set_status(
        self, target_id: int, status: str, *, actor: CurrentUser
    ) -> DatasourceRead:
        require_admin(actor)
        row = await self.repository.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        await self.repository.update(row, {"status": status})
        await self._invalidate(target_id)
        await self.audit.record(actor, "datasource.status", ("datasource", str(target_id)))
        return DatasourceRead.model_validate(row)


def build_service(
    session: AsyncSession,
    box: SecretBox,
    inspector: DatasourceInspector | None = None,
    cache: ScopeCache | None = None,
) -> DatasourceService:
    return DatasourceService(
        DatasourceRepository(session),
        audit_for(session),
        box,
        inspector or DatasourceInspector(),
        cache,
    )


def get_service() -> DatasourceService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
