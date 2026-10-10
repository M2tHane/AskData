from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope
from app.modules.permission.schemas import (
    DatasourceVisibilityWrite,
    FullVisibilityWrite,
    GrantRead,
    MemberGrantsWrite,
    ObjectVisibilityWrite,
    PublicScope,
    TeamGrantsWrite,
    VisibilityRead,
)
from app.modules.permission.service import GrantService, get_service

router = APIRouter(
    prefix="/api/permissions", tags=["permission"], dependencies=[Depends(current_user)]
)
Service = Annotated[GrantService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]


@router.get("/{datasource_id}/scope", response_model=Envelope[PublicScope])
async def scope(datasource_id: int, service: Service, actor: Actor) -> Envelope[PublicScope]:
    return Envelope(data=await service.public_scope(datasource_id, actor=actor))


@router.get("/{datasource_id}/teams/{team_id}", response_model=Envelope[GrantRead])
async def grants(
    datasource_id: int, team_id: int, service: Service, actor: Actor
) -> Envelope[GrantRead]:
    return Envelope(data=await service.read(datasource_id, team_id, actor=actor))


@router.put("/{datasource_id}/teams/{team_id}", response_model=Envelope[GrantRead])
async def replace(
    datasource_id: int, team_id: int, data: TeamGrantsWrite, service: Service, actor: Actor
) -> Envelope[GrantRead]:
    return Envelope(data=await service.replace(datasource_id, team_id, data, actor=actor))


@router.put("/{datasource_id}/teams/{team_id}/members", response_model=Envelope[GrantRead])
async def members(
    datasource_id: int, team_id: int, data: MemberGrantsWrite, service: Service, actor: Actor
) -> Envelope[GrantRead]:
    return Envelope(data=await service.set_members(datasource_id, team_id, data, actor=actor))


@router.get("/{datasource_id}/visibility", response_model=Envelope[VisibilityRead])
async def visibility(
    datasource_id: int, service: Service, actor: Actor
) -> Envelope[VisibilityRead]:
    return Envelope(data=await service.visibility(datasource_id, actor=actor))


@router.put("/{datasource_id}/visibility", response_model=Envelope[VisibilityRead])
async def set_visibility(
    datasource_id: int, data: DatasourceVisibilityWrite, service: Service, actor: Actor
) -> Envelope[VisibilityRead]:
    return Envelope(data=await service.set_datasource_scope(datasource_id, data, actor=actor))


@router.put("/{datasource_id}/visibility/all", response_model=Envelope[VisibilityRead])
async def save_visibility(
    datasource_id: int, data: FullVisibilityWrite, service: Service, actor: Actor
) -> Envelope[VisibilityRead]:
    return Envelope(data=await service.set_visibility_full(datasource_id, data, actor=actor))


@router.delete("/{datasource_id}/visibility/custom", response_model=Envelope[VisibilityRead])
async def reset_visibility(
    datasource_id: int, service: Service, actor: Actor
) -> Envelope[VisibilityRead]:
    return Envelope(data=await service.reset_visibility(datasource_id, actor=actor))


@router.put(
    "/{datasource_id}/visibility/tables/{table_id}", response_model=Envelope[VisibilityRead]
)
async def set_table_visibility(
    datasource_id: int,
    table_id: int,
    data: ObjectVisibilityWrite,
    service: Service,
    actor: Actor,
) -> Envelope[VisibilityRead]:
    return Envelope(data=await service.set_table_scope(datasource_id, table_id, data, actor=actor))


@router.put(
    "/{datasource_id}/visibility/columns/{column_id}", response_model=Envelope[VisibilityRead]
)
async def set_column_visibility(
    datasource_id: int,
    column_id: int,
    data: ObjectVisibilityWrite,
    service: Service,
    actor: Actor,
) -> Envelope[VisibilityRead]:
    return Envelope(
        data=await service.set_column_scope(datasource_id, column_id, data, actor=actor)
    )
