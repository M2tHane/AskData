from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, Page
from app.modules.querylog.schemas import QueryFilter, QueryView
from app.modules.querylog.service import QueryLogService

router = APIRouter(prefix="/querylog", tags=["querylog"], dependencies=[Depends(current_user)])


def get_service() -> QueryLogService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


Service = Annotated[QueryLogService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[QueryView]])
async def list_items(
    filters: Annotated[QueryFilter, Depends()], service: Service, user: User
) -> Envelope[Page[QueryView]]:
    return Envelope(data=await service.list(user, filters))


@router.get("/{identifier}", response_model=Envelope[QueryView])
async def get_item(identifier: int, service: Service, user: User) -> Envelope[QueryView]:
    return Envelope(data=await service.get(identifier, user))
