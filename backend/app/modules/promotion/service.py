from app.core.audit import AuditService, DatabaseAuditStore
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.modules.metric.service import MetricService
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionInput, PromotionView, ReviewInput
from app.modules.semantic.service import SemanticService


class PromotionService:
    def __init__(
        self,
        repository: PromotionRepository,
        semantic: SemanticService,
        metric: MetricService,
        audit: AuditService,
    ) -> None:
        # All three repositories and audit store must share the request transaction.
        self.repository, self.semantic, self.metric, self.audit = (
            repository,
            semantic,
            metric,
            audit,
        )

    async def list(self, actor: CurrentUser) -> list[PromotionView]:
        if not actor.active:
            raise PermissionDenied()
        return [PromotionView.model_validate(row) for row in await self.repository.visible(actor)]

    async def request(self, data: PromotionInput, *, actor: CurrentUser) -> PromotionView:
        if actor.role != Role.MEMBER or actor.team_id is None:
            raise PermissionDenied()
        async with self.repository.atomic():
            service = self.semantic if data.kind == "semantic" else self.metric
            await service.promote(data.ref_id, actor=actor)
            row = await self.repository.create(
                {
                    **data.model_dump(),
                    "semantic_id": data.ref_id if data.kind == "semantic" else None,
                    "metric_id": data.ref_id if data.kind == "metric" else None,
                    "requested_by": actor.id,
                    "team_id": actor.team_id,
                    "reviewed_by": None,
                    "status": "pending",
                    "comment": "",
                },
                actor.id,
            )
            await self.audit.record(actor, "promotion.request", ("promotion", str(row.id)))
            return PromotionView.model_validate(row)

    async def review(
        self, identifier: int, data: ReviewInput, *, actor: CurrentUser
    ) -> PromotionView:
        async with self.repository.atomic():
            row = await self.repository.locked(identifier)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, http_status=404)
            if not actor.active or actor.role != Role.LEADER or actor.team_id != row.team_id:
                raise PermissionDenied()
            if row.status != "pending" or (not data.approved and not data.comment.strip()):
                raise AppError(ErrorCode.CONFLICT)
            service = self.semantic if row.kind == "semantic" else self.metric
            await service.promote(
                row.ref_id, actor=actor, review=data.approved, reason=data.comment
            )
            await self.repository.update(
                row,
                {
                    "status": "approved" if data.approved else "rejected",
                    "reviewed_by": actor.id,
                    "comment": data.comment,
                },
            )
            await self.audit.record(
                actor, "promotion.review", ("promotion", str(row.id)), data.model_dump()
            )
            return PromotionView.model_validate(row)


def build_service(
    session: "AsyncSession", semantic: SemanticService, metric: MetricService
) -> PromotionService:
    return PromotionService(
        PromotionRepository(session), semantic, metric, AuditService(DatabaseAuditStore(session))
    )
