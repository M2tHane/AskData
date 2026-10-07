from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope
from app.modules.permission.schemas import (
    GrantRead,
    MemberGrantsWrite,
    PublicScope,
    TeamGrantsWrite,
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
