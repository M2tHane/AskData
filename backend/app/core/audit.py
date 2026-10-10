from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditFields, Base
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.logging import redact
from app.core.masking import mask_rows
from app.core.sql.executor import QueryResult
from app.core.sql.guard import GuardedSql


class AuditRecord(BaseModel):
    actor_id: int
    action: str
    target_type: str
    target_id: str
    diff: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(days=90))


class QueryLogRecord(BaseModel):
    user_id: int
    team_id: int | None
    datasource_id: int
    status: str
    blocked_code: ErrorCode | None = None
    normalized_sql: str | None = None
    row_count: int = 0
    result_preview: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    duration_ms: int = 0
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    # Optional business snapshot, kept in the existing payload for backwards compatibility.
    details: dict[str, Any] = Field(default_factory=dict)
    conversation_id: int | None = None
    message_id: int | None = None
    query_id: str | None = Field(default=None, min_length=1, max_length=128)
    question: str = ""
    generated_sql: str | None = None
    error: str | None = None
    stage_timings: dict[str, int] = Field(default_factory=dict)
    model_id: str = ""
    chart_spec: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(days=90))


class AuditStore(Protocol):
    async def append(self, record: AuditRecord) -> None: ...


class QueryLogStore(Protocol):
    async def append(self, record: QueryLogRecord) -> int | None: ...


class AuditLog(AuditFields, Base):
    __tablename__ = "audit_log"
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class QueryRun(AuditFields, Base):
    __tablename__ = "query_run"
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DatabaseAuditStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, record: AuditRecord) -> None:
        self.session.add(
            AuditLog(
                payload=record.model_dump(mode="json"),
                created_by=record.actor_id,
                expires_at=record.expires_at,
            )
        )
        await self.session.flush()


class DatabaseQueryLogStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, record: QueryLogRecord) -> int:
        row = QueryRun(
            payload=record.model_dump(mode="json"),
            created_by=record.user_id,
            expires_at=record.expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id


class DetachedQueryLogStore:
    """Commits each record in its own session.

    A disconnected stream's request session may be closing (or mid-statement) when its
    cancelled run is recorded; reusing it corrupts the pooled connection for the next request.
    """

    def __init__(self, sessions: Callable[[], Any]) -> None:
        self.sessions = sessions

    async def append(self, record: QueryLogRecord) -> int:
        async with self.sessions() as session:
            row_id = await DatabaseQueryLogStore(session).append(record)
            await session.commit()
            return row_id


class AuditService:
    def __init__(self, store: AuditStore, retention_days: int = 90) -> None:
        self.store, self.retention_days = store, retention_days

    async def record(
        self,
        actor: CurrentUser,
        action: str,
        target: tuple[str, str],
        diff: dict[str, Any] | None = None,
    ) -> None:
        await self.store.append(
            AuditRecord(
                actor_id=actor.id,
                action=action,
                target_type=target[0],
                target_id=target[1],
                diff=redact(diff or {}),
                expires_at=datetime.now(UTC) + timedelta(days=self.retention_days),
            )
        )


def audited(
    action: str, target_type: str
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Write services accept actor as a keyword and share one transaction with the audit store."""

    def decorate(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(function)
        async def wrapper(service: Any, *args: Any, **kwargs: Any) -> Any:
            actor = kwargs.get("actor")
            if not isinstance(actor, CurrentUser) or not isinstance(service.audit, AuditService):
                raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
            result = await function(service, *args, **kwargs)
            target_id = str(getattr(result, "id", kwargs.get("target_id", "")))
            await service.audit.record(actor, action, (target_type, target_id))
            return result

        return wrapper

    return decorate


class QueryLogWriter:
    def __init__(self, store: QueryLogStore, retention_days: int = 90) -> None:
        self.store, self.retention_days = store, retention_days

    async def write(
        self,
        user: CurrentUser,
        datasource_id: int,
        status: str,
        sql: GuardedSql | None = None,
        result: QueryResult | None = None,
        blocked_code: ErrorCode | None = None,
        prompt_versions: dict[str, str] | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        *,
        details: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> int | None:
        if sql is not None and (not sql.authentic() or sql.datasource_id != datasource_id):
            raise AppError(ErrorCode.SQL_OUT_OF_SCOPE)
        if result is not None and sql is None:
            raise AppError(ErrorCode.SQL_NOT_SELECT)
        preview = mask_rows(result.rows[:5], sql.output_masks) if result and sql else []
        return await self.store.append(
            QueryLogRecord(
                user_id=user.id,
                team_id=user.team_id,
                datasource_id=datasource_id,
                status=status,
                blocked_code=blocked_code,
                normalized_sql=sql.normalized_sql if sql else None,
                row_count=result.row_count if result else 0,
                result_preview=preview,
                duration_ms=duration_ms
                if duration_ms is not None
                else (result.duration_ms if result else 0),
                details=redact(details or {}),
                conversation_id=(details or {}).get("conversation_id"),
                message_id=(details or {}).get("message_id"),
                query_id=(details or {}).get("query_id"),
                question=(details or {}).get("question", ""),
                generated_sql=(details or {}).get("generated_sql"),
                error=(details or {}).get("error"),
                stage_timings=(details or {}).get("stage_timings", {}),
                model_id=(details or {}).get("model_id", ""),
                chart_spec=(details or {}).get("chart") or {},
                retry_count=len((details or {}).get("retries", [])),
                context_snapshot=redact(details or {}),
                prompt_versions=prompt_versions or {},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                expires_at=datetime.now(UTC) + timedelta(days=self.retention_days),
            )
        )
