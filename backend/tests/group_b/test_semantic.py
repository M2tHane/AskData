import pytest
from sqlalchemy import func, select

from app.core.audit import AuditLog
from app.core.errors import AppError, ErrorCode
from app.core.scope import ScopeLevel
from app.modules.semantic.models import SemanticTerm
from app.modules.semantic.schemas import SemanticInput


async def test_private_visibility_and_version_audit(b_services, member, leader, admin, outsider):
    service = b_services.semantic
    data = SemanticInput(
        datasource_id=1,
        target_type="column",
        target_id=1,
        aliases=["order number"],
        description="stable definition",
    )
    saved = await service.save(data, actor=member)
    assert saved.scope == ScopeLevel.PERSONAL and saved.version == 1
    assert [item.id for item in await service.list(member, 1)] == [saved.id]
    for viewer in (leader, admin, outsider):
        assert await service.list(viewer, 1) == []
        with pytest.raises(AppError):
            await service.get(saved.id, viewer)
        with pytest.raises(AppError):
            await service.save(data, identifier=saved.id, actor=viewer)
    revised = await service.save(
        data.model_copy(update={"description": "new definition"}), actor=member, identifier=saved.id
    )
    assert revised.version == 2
    audit = (await b_services.session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    assert audit[-1].payload["diff"]["before"]["description"] == "stable definition"
    assert b_services.index.events[-1] == ("semantic", saved.id, 1)
    await service.delete(saved.id, actor=member)
    assert await service.list(member, 1) == []


async def test_role_scope_and_live_authorization(b_services, member, leader, admin):
    data = SemanticInput(datasource_id=1, target_type="column", target_id=1)
    for actor, scope in [
        (member, ScopeLevel.TEAM),
        (leader, ScopeLevel.PLATFORM),
        (admin, ScopeLevel.PERSONAL),
    ]:
        with pytest.raises(AppError):
            await b_services.semantic.save(data.model_copy(update={"scope": scope}), actor=actor)
    with pytest.raises(AppError):
        await b_services.semantic.save(data.model_copy(update={"target_id": 3}), actor=member)
    with pytest.raises(AppError):
        await b_services.semantic.save(
            data.model_copy(update={"target_type": "table"}), actor=member
        )
    saved = await b_services.semantic.save(data, actor=member)
    b_services.provider.grants[0].member_columns = frozenset({"phone"})
    assert await b_services.semantic.list(member, 1) == []
    with pytest.raises(AppError):
        await b_services.semantic.get(saved.id, member)


async def test_audit_failure_rolls_back_source(b_services, member):
    class FailingAudit:
        async def record(self, *args, **kwargs):
            raise AppError(ErrorCode.INTERNAL_ERROR)

    b_services.semantic.audit = FailingAudit()
    with pytest.raises(AppError):
        await b_services.semantic.save(
            SemanticInput(datasource_id=1, target_type="column", target_id=1), actor=member
        )
    assert await b_services.session.scalar(select(func.count()).select_from(SemanticTerm)) == 0


async def test_admin_edits_team_definitions_without_taking_ownership(b_services, leader, admin):
    data = SemanticInput(
        datasource_id=1,
        target_type="column",
        target_id=1,
        aliases=["单号"],
        scope=ScopeLevel.TEAM,
    )
    saved = await b_services.semantic.save(data, actor=leader)
    edited = await b_services.semantic.save(
        data.model_copy(update={"description": "管理员修订"}), identifier=saved.id, actor=admin
    )
    assert edited.scope == ScopeLevel.TEAM and edited.description == "管理员修订"
    assert edited.owner_team_id == saved.owner_team_id
    await b_services.semantic.delete(saved.id, actor=admin)
    assert await b_services.semantic.list(leader, 1) == []
