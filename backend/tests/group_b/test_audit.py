import pytest

from app.core.errors import AppError
from app.modules.audit.repository import AuditRepository
from app.modules.audit.schemas import AuditFilter
from app.modules.audit.service import AuditQueryService


async def test_admin_audit_pagination_filters_and_redaction(b_services, member, leader, admin):
    await b_services.audit.record(
        member, "metric.save", ("metric", "42"), {"description": "safe", "api_key": "secret"}
    )
    await b_services.audit.record(admin, "semantic.save", ("semantic", "1"))
    service = AuditQueryService(AuditRepository(b_services.session))
    for actor in (member, leader):
        with pytest.raises(AppError):
            await service.list(actor, AuditFilter())
    page = await service.list(
        admin, AuditFilter(actor_id=member.id, action="metric.save", page_size=1)
    )
    assert page.total == 1
    assert page.items[0].diff["api_key"] == "[REDACTED]"
    assert "secret" not in page.model_dump_json()
