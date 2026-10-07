from datetime import datetime

from app.core.audit import AuditRecord
from app.core.response import PageParams


class AuditView(AuditRecord):
    id: int


class AuditFilter(PageParams):
    actor_id: int | None = None
    action: str | None = None
    target_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
