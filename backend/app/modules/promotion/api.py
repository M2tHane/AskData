from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.modules.promotion.schemas import PromotionInput, PromotionView, ReviewInput
from app.modules.promotion.service import PromotionService

router = APIRouter(prefix="/promotion", tags=["promotion"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> PromotionService:
    service = getattr(request.state, "promotion_service", None)
    if not isinstance(service, PromotionService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


Service = Annotated[PromotionService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[list[PromotionView]])
async def list_items(service: Service, user: User) -> Envelope[list[PromotionView]]:
    return Envelope(data=await service.list(user))


@router.post("", response_model=Envelope[PromotionView])
async def request_promotion(
    data: PromotionInput, service: Service, user: User
) -> Envelope[PromotionView]:
    return Envelope(data=await service.request(data, actor=user))


@router.post("/{identifier}/review", response_model=Envelope[PromotionView])
async def review_promotion(
    identifier: int, data: ReviewInput, service: Service, user: User
) -> Envelope[PromotionView]:
    return Envelope(data=await service.review(identifier, data, actor=user))
