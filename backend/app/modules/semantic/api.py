from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.modules.semantic.schemas import SemanticInput, SemanticView
from app.modules.semantic.service import SemanticService

router = APIRouter(prefix="/semantic", tags=["semantic"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> SemanticService:
    service = getattr(request.state, "semantic_service", None)
    if not isinstance(service, SemanticService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


Service = Annotated[SemanticService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[list[SemanticView]])
async def list_items(
    datasource_id: int, service: Service, user: User
) -> Envelope[list[SemanticView]]:
    return Envelope(data=await service.list(user, datasource_id))


@router.get("/{identifier}", response_model=Envelope[SemanticView])
async def get_item(identifier: int, service: Service, user: User) -> Envelope[SemanticView]:
    return Envelope(data=await service.get(identifier, user))


@router.post("", response_model=Envelope[SemanticView])
async def create_item(data: SemanticInput, service: Service, user: User) -> Envelope[SemanticView]:
    return Envelope(data=await service.save(data, actor=user))


@router.put("/{identifier}", response_model=Envelope[SemanticView])
async def update_item(
    identifier: int, data: SemanticInput, service: Service, user: User
) -> Envelope[SemanticView]:
    return Envelope(data=await service.save(data, identifier=identifier, actor=user))


@router.delete("/{identifier}", response_model=Envelope[bool])
async def delete_item(identifier: int, service: Service, user: User) -> Envelope[bool]:
    await service.delete(identifier, actor=user)
    return Envelope(data=True)
