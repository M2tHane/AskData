from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.response import Page
from app.modules.audit.repository import AuditRepository
from app.modules.audit.schemas import AuditFilter, AuditView


class AuditQueryService:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    async def list(self, actor: CurrentUser, filters: AuditFilter) -> Page[AuditView]:
        if not actor.active or actor.role != Role.ADMIN:
            raise PermissionDenied()
        if filters.since and filters.until and filters.since >= filters.until:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        rows, total = await self.repository.search(filters)
        return Page(
            items=[AuditView.model_validate({**row.payload, "id": row.id}) for row in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )


def build_service(session: "AsyncSession") -> AuditQueryService:
    return AuditQueryService(AuditRepository(session))
