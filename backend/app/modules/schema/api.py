from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.permission import PermissionService
from app.core.response import Envelope
from app.core.tasks import TaskManager, TaskRecord
from app.modules.schema.schemas import (
    ColumnRead,
    DescriptionWrite,
    DraftRequest,
    DraftResult,
    RelationRead,
    SetupRead,
    SetupResult,
    SetupSummary,
    SetupWrite,
    TableRead,
)
from app.modules.schema.service import (
    SchemaService,
    get_grants,
    get_permissions,
    get_service,
    get_task_manager,
)

router = APIRouter(prefix="/api/schema", tags=["schema"], dependencies=[Depends(current_user)])
Service = Annotated[SchemaService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]
Permissions = Annotated[PermissionService, Depends(get_permissions)]
Manager = Annotated[TaskManager, Depends(get_task_manager)]
Grants = Annotated[Any, Depends(get_grants)]


@router.get("/setup-summary", response_model=Envelope[list[SetupSummary]])
async def setup_summary(service: Service, actor: Actor) -> Envelope[list[SetupSummary]]:
    """Per datasource: tables waiting to be imported and columns waiting for a semantic."""
    return Envelope(data=await service.summaries(actor=actor))


@router.get("/{datasource_id}/setup", response_model=Envelope[SetupRead])
async def setup(datasource_id: int, service: Service, actor: Actor) -> Envelope[SetupRead]:
    return Envelope(data=await service.setup(datasource_id, actor=actor))


@router.put("/{datasource_id}/setup", response_model=Envelope[SetupResult])
async def save_setup(
    datasource_id: int, data: SetupWrite, service: Service, grants: Grants, actor: Actor
) -> Envelope[SetupResult]:
    return Envelope(data=await service.save_setup(datasource_id, data, grants, actor=actor))


@router.post("/{datasource_id}/setup/draft", response_model=Envelope[DraftResult])
async def draft_semantics(
    datasource_id: int, data: DraftRequest, service: Service, actor: Actor
) -> Envelope[DraftResult]:
    """Model-proposed semantics for columns; nothing is saved."""
    return Envelope(data=await service.draft(datasource_id, data, actor=actor))


@router.get("/{datasource_id}/tables", response_model=Envelope[list[TableRead]])
async def tables(
    datasource_id: int, service: Service, permissions: Permissions, actor: Actor
) -> Envelope[list[TableRead]]:
    return Envelope(data=await service.browse(datasource_id, permissions, actor=actor))


@router.get("/{datasource_id}/relations", response_model=Envelope[list[RelationRead]])
async def relations(
    datasource_id: int, service: Service, permissions: Permissions, actor: Actor
) -> Envelope[list[RelationRead]]:
    return Envelope(data=await service.relations(datasource_id, permissions, actor=actor))


@router.patch("/tables/{target_id}", response_model=Envelope[TableRead])
async def describe_table(
    target_id: int, data: DescriptionWrite, service: Service, actor: Actor
) -> Envelope[TableRead]:
    return Envelope(data=await service.describe_table(target_id, data.description, actor=actor))


@router.patch("/columns/{target_id}", response_model=Envelope[ColumnRead])
async def describe_column(
    target_id: int, data: DescriptionWrite, service: Service, actor: Actor
) -> Envelope[ColumnRead]:
    return Envelope(data=await service.describe_column(target_id, data.description, actor=actor))


@router.post("/{datasource_id}/sync", response_model=Envelope[TaskRecord])
async def sync(
    datasource_id: int, service: Service, manager: Manager, actor: Actor
) -> Envelope[TaskRecord]:
    return Envelope(data=await service.enqueue(datasource_id, manager, actor=actor))


@router.get("/tasks/{task_id}", response_model=Envelope[TaskRecord])
async def task(
    task_id: str, service: Service, manager: Manager, actor: Actor
) -> Envelope[TaskRecord]:
    return Envelope(data=await service.task(task_id, manager, actor=actor))
