from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.datasource import ConnectionTest
from app.core.deps import CurrentUser, current_user
from app.core.permission import PermissionService
from app.core.response import Envelope, Page, PageParams
from app.modules.datasource.schemas import (
    DatasourceChoice,
    DatasourceRead,
    DatasourceStatus,
    DatasourceWrite,
)
from app.modules.datasource.service import DatasourceService, get_service
from app.modules.schema.service import get_permissions

router = APIRouter(
    prefix="/api/datasources", tags=["datasource"], dependencies=[Depends(current_user)]
)
Service = Annotated[DatasourceService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[DatasourceRead]])
async def listing(
    service: Service, actor: Actor, page: Annotated[PageParams, Depends()]
) -> Envelope[Page[DatasourceRead]]:
    return Envelope(data=await service.listing(page, actor=actor))


@router.get("/available", response_model=Envelope[Page[DatasourceChoice]])
async def available(
    service: Service,
    actor: Actor,
    page: Annotated[PageParams, Depends()],
    permissions: Annotated[PermissionService, Depends(get_permissions)],
) -> Envelope[Page[DatasourceChoice]]:
    return Envelope(data=await service.choices(page, permissions, actor=actor))


@router.post("/test", response_model=Envelope[ConnectionTest])
async def test_draft(
    data: DatasourceWrite, service: Service, actor: Actor
) -> Envelope[ConnectionTest]:
    return Envelope(data=await service.test_draft(data, actor=actor))


@router.post("", response_model=Envelope[DatasourceRead])
async def create(data: DatasourceWrite, service: Service, actor: Actor) -> Envelope[DatasourceRead]:
    return Envelope(data=await service.create(data, actor=actor))


@router.put("/{target_id}", response_model=Envelope[DatasourceRead])
async def update(
    target_id: int, data: DatasourceWrite, service: Service, actor: Actor
) -> Envelope[DatasourceRead]:
    return Envelope(data=await service.update(target_id, data, actor=actor))


@router.post("/{target_id}/test", response_model=Envelope[ConnectionTest])
async def test_saved(target_id: int, service: Service, actor: Actor) -> Envelope[ConnectionTest]:
    return Envelope(data=await service.test(target_id, actor=actor))


@router.patch("/{target_id}/status", response_model=Envelope[DatasourceRead])
async def status(
    target_id: int, data: DatasourceStatus, service: Service, actor: Actor
) -> Envelope[DatasourceRead]:
    return Envelope(data=await service.set_status(target_id, data.status, actor=actor))
