"""Upload composition boundary; all persistence stays in the owning services.

The ports below are required public service extensions, documented in INTEGRATION.md.
The factory fails closed if the supplied services do not yet implement them. Every
publication step must share the upload transaction and datasource lock; receipts must
be durable, so retries after a worker restart cannot extend grants.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.datasource import Datasource, TableMetadata
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.permission import PermissionService
from app.modules.schema.service import SyncResult


class UploadPublication(BaseModel):
    datasource_id: int = Field(ge=1)
    owner_team_id: int = Field(ge=1)
    completed: bool = False
    initialize_owner_grants: bool = False


@runtime_checkable
class UploadDatasourceService(Protocol):
    async def reserve_upload(self, name: str, team_id: int, *, actor: CurrentUser) -> int:
        """Create an ungranted pending upload without testing a reader that does not exist."""
        ...

    async def upload_owner(self, datasource_id: int) -> int:
        """Read owner through service; reject non-upload sources, including pending ones."""
        ...

    async def publish_upload_credentials(
        self, job_id: int, datasource: Datasource, owner_team_id: int, *, actor: CurrentUser
    ) -> UploadPublication:
        """Lock source, validate owner/type, encrypt reader secret, return durable receipt.

        initialize_owner_grants remains true until the FIRST publication commits.
        Retrying a completed job returns completed=True. All steps share a transaction.
        """
        ...

    async def finish_upload_publication(
        self, job_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None: ...


@runtime_checkable
class UploadSchemaService(Protocol):
    async def synchronize_upload(
        self, datasource_id: int, table: TableMetadata, *, actor: CurrentUser
    ) -> SyncResult:
        """Upsert only this table, preserve other sheets/manual descriptions and comments."""
        ...


@runtime_checkable
class UploadGrantService(Protocol):
    async def expire_metadata(self, result: SyncResult) -> None: ...

    async def grant_upload_owner(
        self, datasource_id: int, team_id: int, *, actor: CurrentUser
    ) -> None:
        """Initial owner-team-only grants; validate upload ownership with the real actor.

        Do not grant other teams or relax existing masks. Subsequent uploads never
        call this method, so new columns remain ungranted.
        """
        ...


@runtime_checkable
class UploadOrgService(Protocol):
    async def ensure_team(self, team_id: int) -> object: ...


class ServiceUploadRegistration:
    def __init__(
        self,
        datasources: UploadDatasourceService,
        schema: UploadSchemaService,
        grants: UploadGrantService,
        org: UploadOrgService,
        permissions: PermissionService,
    ) -> None:
        self.datasources, self.schema, self.grants = datasources, schema, grants
        self.org, self.permissions = org, permissions

    @staticmethod
    def _owner_actor(team_id: int, actor: CurrentUser) -> None:
        if not actor.active or (
            actor.role != Role.ADMIN and (actor.role != Role.LEADER or actor.team_id != team_id)
        ):
            raise PermissionDenied()
        if team_id < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)

    async def reserve(self, name: str, team_id: int, actor: CurrentUser) -> int:
        self._owner_actor(team_id, actor)
        await self.org.ensure_team(team_id)
        return await self.datasources.reserve_upload(name, team_id, actor=actor)

    async def owner_team(self, datasource_id: int) -> int:
        return await self.datasources.upload_owner(datasource_id)

    async def publish(
        self,
        job_id: int,
        datasource: Datasource,
        table: TableMetadata,
        owner_team_id: int,
        actor: CurrentUser,
    ) -> None:
        self._owner_actor(owner_team_id, actor)
        if (
            job_id < 1
            or datasource.id < 1
            or not datasource.read_only
            or datasource.dialect != "postgres"
            or datasource.schema_name != f"ds_{datasource.id}"
            or table.schema_name != datasource.schema_name
        ):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if await self.owner_team(datasource.id) != owner_team_id:
            raise PermissionDenied()
        receipt = await self.datasources.publish_upload_credentials(
            job_id, datasource, owner_team_id, actor=actor
        )
        if receipt.datasource_id != datasource.id or receipt.owner_team_id != owner_team_id:
            raise AppError(ErrorCode.CONFLICT)
        if receipt.completed:
            return
        result = await self.schema.synchronize_upload(datasource.id, table, actor=actor)
        if result.datasource_id != datasource.id:
            raise AppError(ErrorCode.CONFLICT)
        await self.grants.expire_metadata(result)
        if receipt.initialize_owner_grants:
            await self.grants.grant_upload_owner(datasource.id, owner_team_id, actor=actor)
        await self.permissions.invalidate(datasource.id)
        await self.datasources.finish_upload_publication(job_id, datasource.id, actor=actor)


def build_upload_registration(
    datasources: object,
    schema: object,
    grants: object,
    org: object,
    permissions: PermissionService,
) -> ServiceUploadRegistration:
    """I3 binds transaction-scoped public services here; no privileged actor fallback."""
    if not (
        isinstance(datasources, UploadDatasourceService)
        and isinstance(schema, UploadSchemaService)
        and isinstance(grants, UploadGrantService)
        and isinstance(org, UploadOrgService)
    ):
        raise AppError(
            ErrorCode.TASK_FAILED,
            "Upload registration services are not configured",
            http_status=503,
        )
    return ServiceUploadRegistration(datasources, schema, grants, org, permissions)
