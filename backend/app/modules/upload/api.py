from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, Role, require_role
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, PageParams
from app.modules.upload.schemas import ConfirmRequest, JobView, Preview, PreviewRequest
from app.modules.upload.service import UploadService

router = APIRouter(prefix="/upload", tags=["upload"])
Actor = Annotated[CurrentUser, Depends(require_role(Role.ADMIN, Role.LEADER))]


def upload_service() -> UploadService:
    """Composition root overrides with a request-scoped repository and service adapters."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


Service = Annotated[UploadService, Depends(upload_service)]


@router.post("/preview", response_model=Envelope[Preview])
async def preview(request: PreviewRequest, service: Service, actor: Actor) -> Envelope[Preview]:
    return Envelope(data=await service.preview(request, actor=actor))


@router.post("/{identifier}/confirm", response_model=Envelope[JobView])
async def confirm(
    identifier: int,
    request: ConfirmRequest,
    service: Service,
    actor: Actor,
) -> Envelope[JobView]:
    return Envelope(data=await service.confirm(identifier, request, actor=actor))


@router.post("/{identifier}/publish", response_model=Envelope[JobView])
async def publish(identifier: int, service: Service, actor: Actor) -> Envelope[JobView]:
    return Envelope(data=await service.publish(identifier, actor=actor))


@router.get("/history/{datasource_id}", response_model=Envelope[list[JobView]])
async def history(
    datasource_id: int,
    service: Service,
    actor: Actor,
    page: Annotated[PageParams, Depends()],
) -> Envelope[list[JobView]]:
    return Envelope(
        data=await service.history(
            datasource_id,
            page.offset,
            page.page_size,
            actor=actor,
        )
    )


@router.get("/{identifier}", response_model=Envelope[JobView])
async def get(identifier: int, service: Service, actor: Actor) -> Envelope[JobView]:
    return Envelope(data=await service.get(identifier, actor=actor))
