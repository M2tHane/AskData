from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, Page, PageParams
from app.modules.usage.schemas import QuotaInput, QuotaView, UsageView
from app.modules.usage.service import UsageService

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> UsageService:
    service = getattr(request.state, "usage_service", None)
    if not isinstance(service, UsageService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


Service = Annotated[UsageService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[UsageView]])
async def list_items(
    page: Annotated[PageParams, Depends()],
    service: Service,
    user: User,
    since: date | None = None,
    until: date | None = None,
) -> Envelope[Page[UsageView]]:
    return Envelope(data=await service.list(user, page, since, until))


@router.get("/quota/{team_id}", response_model=Envelope[QuotaView])
async def get_quota(team_id: int, service: Service, user: User) -> Envelope[QuotaView]:
    return Envelope(data=await service.get_quota(team_id, user))


@router.put("/quota/{team_id}", response_model=Envelope[QuotaView])
async def set_quota(
    team_id: int, data: QuotaInput, service: Service, user: User
) -> Envelope[QuotaView]:
    return Envelope(data=await service.set_quota(team_id, data, actor=user))
