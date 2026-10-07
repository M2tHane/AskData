from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope, Page, PageParams
from app.modules.org.schemas import TeamRead, TeamWrite, UserRead, UserUpdate, UserWrite
from app.modules.org.service import OrgService, get_service

router = APIRouter(prefix="/api/org", tags=["org"], dependencies=[Depends(current_user)])
Service = Annotated[OrgService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]
Pagination = Annotated[PageParams, Depends()]


@router.get("/teams", response_model=Envelope[Page[TeamRead]])
async def teams(service: Service, actor: Actor, page: Pagination) -> Envelope[Page[TeamRead]]:
    return Envelope(data=await service.list_teams(page, actor=actor))


@router.post("/teams", response_model=Envelope[TeamRead])
async def create_team(data: TeamWrite, service: Service, actor: Actor) -> Envelope[TeamRead]:
    return Envelope(data=await service.create_team(data, actor=actor))


@router.put("/teams/{target_id}", response_model=Envelope[TeamRead])
async def update_team(
    target_id: int, data: TeamWrite, service: Service, actor: Actor
) -> Envelope[TeamRead]:
    return Envelope(data=await service.update_team(target_id, data, actor=actor))


@router.get("/users", response_model=Envelope[Page[UserRead]])
async def users(service: Service, actor: Actor, page: Pagination) -> Envelope[Page[UserRead]]:
    return Envelope(data=await service.list_users(page, actor=actor))


@router.post("/users", response_model=Envelope[UserRead])
async def create_user(data: UserWrite, service: Service, actor: Actor) -> Envelope[UserRead]:
    return Envelope(data=await service.create_user(data, actor=actor))


@router.put("/users/{target_id}", response_model=Envelope[UserRead])
async def update_user(
    target_id: int, data: UserUpdate, service: Service, actor: Actor
) -> Envelope[UserRead]:
    return Envelope(data=await service.update_user(target_id, data, actor=actor))
