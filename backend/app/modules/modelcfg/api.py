from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope, Page, PageParams
from app.modules.modelcfg.schemas import (
    ModelKind,
    ModelRead,
    ModelTestResult,
    ModelWrite,
    PreferenceRead,
    PreferenceWrite,
)
from app.modules.modelcfg.service import ModelConfigService, get_service

router = APIRouter(prefix="/api/models", tags=["modelcfg"], dependencies=[Depends(current_user)])
Service = Annotated[ModelConfigService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[ModelRead]])
async def listing(
    service: Service, actor: Actor, page: Annotated[PageParams, Depends()]
) -> Envelope[Page[ModelRead]]:
    return Envelope(data=await service.list(page, actor=actor))


@router.post("", response_model=Envelope[ModelRead])
async def create(data: ModelWrite, service: Service, actor: Actor) -> Envelope[ModelRead]:
    return Envelope(data=await service.create(data, actor=actor))


@router.put("/{target_id}", response_model=Envelope[ModelRead])
async def update(
    target_id: int, data: ModelWrite, service: Service, actor: Actor
) -> Envelope[ModelRead]:
    return Envelope(data=await service.update(target_id, data, actor=actor))


@router.post("/{target_id}/test", response_model=Envelope[ModelTestResult])
async def test(target_id: int, service: Service, actor: Actor) -> Envelope[ModelTestResult]:
    return Envelope(data=await service.test(target_id, actor=actor))


@router.get("/teams/{team_id}/{kind}", response_model=Envelope[PreferenceRead])
async def selected(
    team_id: int, kind: ModelKind, service: Service, actor: Actor
) -> Envelope[PreferenceRead]:
    return Envelope(data=await service.get_preference(team_id, kind, actor=actor))


@router.put("/teams/{team_id}/{kind}", response_model=Envelope[PreferenceRead])
async def preference(
    team_id: int, kind: ModelKind, data: PreferenceWrite, service: Service, actor: Actor
) -> Envelope[PreferenceRead]:
    return Envelope(data=await service.set_preference(team_id, kind, data.model_id, actor=actor))
