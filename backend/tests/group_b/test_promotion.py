import pytest

from app.core.errors import AppError
from app.core.scope import ScopeLevel
from app.modules.metric.schemas import MetricInput
from app.modules.promotion.schemas import PromotionInput, ReviewInput
from app.modules.semantic.schemas import SemanticInput


@pytest.mark.parametrize("kind", ["semantic", "metric"])
async def test_promotion_state_machine(b_services, member, leader, admin, outsider, kind):
    service = b_services.semantic if kind == "semantic" else b_services.metric
    data = (
        SemanticInput(datasource_id=1, target_type="column", target_id=1)
        if kind == "semantic"
        else MetricInput(datasource_id=1, name="order", sql_template="SELECT id FROM orders")
    )
    item = await service.save(data, actor=member)
    promotion = await b_services.promotion.request(
        PromotionInput(kind=kind, ref_id=item.id), actor=member
    )
    assert promotion.status == "pending"
    assert await service.list(leader, 1) == []
    with pytest.raises(AppError):
        await b_services.promotion.request(PromotionInput(kind=kind, ref_id=item.id), actor=member)
    with pytest.raises(AppError):
        await service.save(data, identifier=item.id, actor=member)
    for actor in (admin, outsider):
        with pytest.raises(AppError):
            await b_services.promotion.review(promotion.id, ReviewInput(approved=True), actor=actor)
    with pytest.raises(AppError):
        await b_services.promotion.review(promotion.id, ReviewInput(approved=False), actor=leader)
    rejected = await b_services.promotion.review(
        promotion.id, ReviewInput(approved=False, comment="needs revision"), actor=leader
    )
    assert rejected.status == "rejected"
    assert (await service.get(item.id, member)).status == "private"
    again = await b_services.promotion.request(
        PromotionInput(kind=kind, ref_id=item.id), actor=member
    )
    approved = await b_services.promotion.review(again.id, ReviewInput(approved=True), actor=leader)
    assert approved.status == "approved"
    assert (await service.get(item.id, member)).scope == ScopeLevel.TEAM
    assert (await service.get(item.id, leader)).version == 5
    if kind == "metric":
        assert len(b_services.connector.executed) == 2
    with pytest.raises(AppError):
        await b_services.promotion.review(again.id, ReviewInput(approved=True), actor=leader)


async def test_review_rechecks_revoked_permissions(b_services, member, leader):
    item = await b_services.semantic.save(
        SemanticInput(datasource_id=1, target_type="column", target_id=1), actor=member
    )
    request = await b_services.promotion.request(
        PromotionInput(kind="semantic", ref_id=item.id), actor=member
    )
    b_services.provider.grants[0].team_columns = frozenset({"phone"})
    with pytest.raises(AppError):
        await b_services.promotion.review(request.id, ReviewInput(approved=True), actor=leader)
    assert (await b_services.promotion.list(member))[0].status == "pending"
