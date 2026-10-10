from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope
from app.modules.promptcfg.schemas import (
    PromptDetail,
    PromptKind,
    PromptSummary,
    RevisionRead,
    RevisionWrite,
)
from app.modules.promptcfg.service import PromptConfigService, get_service

router = APIRouter(prefix="/api/prompts", tags=["prompts"], dependencies=[Depends(current_user)])
Service = Annotated[PromptConfigService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[list[PromptSummary]])
async def overview(service: Service, actor: Actor) -> Envelope[list[PromptSummary]]:
    return Envelope(data=await service.overview(actor=actor))


@router.get("/{kind}/{key}", response_model=Envelope[PromptDetail])
async def detail(
    kind: PromptKind, key: str, service: Service, actor: Actor
) -> Envelope[PromptDetail]:
    return Envelope(data=await service.detail(kind, key, actor=actor))


@router.get("/{kind}/{key}/revisions/{identifier}", response_model=Envelope[RevisionRead])
async def revision(
    kind: PromptKind, key: str, identifier: int, service: Service, actor: Actor
) -> Envelope[RevisionRead]:
    return Envelope(data=await service.revision(kind, key, identifier, actor=actor))


@router.post("/{kind}/{key}/revisions", response_model=Envelope[RevisionRead], status_code=201)
async def create(
    kind: PromptKind, key: str, data: RevisionWrite, service: Service, actor: Actor
) -> Envelope[RevisionRead]:
    return Envelope(data=await service.create(kind, key, data, actor=actor))


@router.post("/{kind}/{key}/revisions/{identifier}/activate", response_model=Envelope[None])
async def activate(
    kind: PromptKind, key: str, identifier: int, service: Service, actor: Actor
) -> Envelope[None]:
    await service.activate(kind, key, identifier, actor=actor)
    return Envelope(data=None)


@router.post("/{kind}/{key}/reset", response_model=Envelope[None])
async def reset(kind: PromptKind, key: str, service: Service, actor: Actor) -> Envelope[None]:
    await service.reset(kind, key, actor=actor)
    return Envelope(data=None)
