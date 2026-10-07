from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.modules.metric.schemas import MetricInput, MetricView
from app.modules.metric.service import MetricService

router = APIRouter(prefix="/metric", tags=["metric"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> MetricService:
    service = getattr(request.state, "metric_service", None)
    if not isinstance(service, MetricService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


Service = Annotated[MetricService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[list[MetricView]])
async def list_items(
    datasource_id: int, service: Service, user: User
) -> Envelope[list[MetricView]]:
    return Envelope(data=await service.list(user, datasource_id))


@router.get("/{identifier}", response_model=Envelope[MetricView])
async def get_item(identifier: int, service: Service, user: User) -> Envelope[MetricView]:
    return Envelope(data=await service.get(identifier, user))


@router.post("", response_model=Envelope[MetricView])
async def create_item(data: MetricInput, service: Service, user: User) -> Envelope[MetricView]:
    return Envelope(data=await service.save(data, actor=user))


@router.put("/{identifier}", response_model=Envelope[MetricView])
async def update_item(
    identifier: int, data: MetricInput, service: Service, user: User
) -> Envelope[MetricView]:
    return Envelope(data=await service.save(data, identifier=identifier, actor=user))


@router.delete("/{identifier}", response_model=Envelope[bool])
async def delete_item(identifier: int, service: Service, user: User) -> Envelope[bool]:
    await service.delete(identifier, actor=user)
    return Envelope(data=True)
