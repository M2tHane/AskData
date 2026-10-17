from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, Page
from app.modules.audit.schemas import AuditFilter, AuditView
from app.modules.audit.service import AuditQueryService

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(current_user)])


def get_service() -> AuditQueryService:
    """Placeholder: the composition root overrides it with the request's service."""
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


Service = Annotated[AuditQueryService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[AuditView]])
async def list_items(
    filters: Annotated[AuditFilter, Depends()], service: Service, user: User
) -> Envelope[Page[AuditView]]:
    return Envelope(data=await service.list(user, filters))
