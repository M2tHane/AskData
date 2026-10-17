from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, Page, PageParams
from app.modules.usage.schemas import QuotaInput, QuotaUsage, QuotaView, TrendPoint, UsageView
from app.modules.usage.service import UsageService

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(current_user)])


def get_service() -> UsageService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


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


@router.get("/trend", response_model=Envelope[list[TrendPoint]])
async def trend(
    service: Service,
    user: User,
    by: Literal["team", "model"] = "team",
    since: date | None = None,
    until: date | None = None,
) -> Envelope[list[TrendPoint]]:
    return Envelope(data=await service.trend(user, by, since, until))


@router.get("/alerts", response_model=Envelope[list[QuotaUsage]])
async def alerts(service: Service, user: User) -> Envelope[list[QuotaUsage]]:
    return Envelope(data=await service.quota_usage(user))


@router.get("/quota/{team_id}", response_model=Envelope[QuotaView])
async def get_quota(team_id: int, service: Service, user: User) -> Envelope[QuotaView]:
    return Envelope(data=await service.get_quota(team_id, user))


@router.put("/quota/{team_id}", response_model=Envelope[QuotaView])
async def set_quota(
    team_id: int, data: QuotaInput, service: Service, user: User
) -> Envelope[QuotaView]:
    return Envelope(data=await service.set_quota(team_id, data, actor=user))
