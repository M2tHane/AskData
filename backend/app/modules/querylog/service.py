from typing import Any

from app.core.audit import AuditService, DatabaseAuditStore, QueryLogRecord, QueryLogWriter
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.logging import redact
from app.core.response import Page
from app.core.sql.executor import QueryResult
from app.core.sql.guard import GuardedSql
from app.modules.querylog.repository import QueryLogRepository
from app.modules.querylog.schemas import QueryFilter, QueryMetadata, QueryView


class _EnrichedStore:
    def __init__(
        self, repository: QueryLogRepository, metadata: QueryMetadata, status: str
    ) -> None:
        self.repository = repository
        self.metadata = metadata.model_dump(mode="json")
        # Only trusted summary context, never a raw QueryContext with full results or credentials.
        self.metadata["context_snapshot"] = redact(
            {
                key: value
                for key, value in metadata.context_snapshot.items()
                if key
                in {
                    "semantic",
                    "metrics",
                    "examples",
                    "prompt_versions",
                    "model_id",
                    "filters",
                    "time_range",
                    "retry_count",
                    "versions",
                }
            }
        )
        if status == "blocked":
            self.metadata.update(generated_sql=None, context_snapshot={}, chart_spec={})

    async def append(self, record: QueryLogRecord) -> int:
        return await self.repository.append(record, self.metadata)


class QueryLogService:
    def __init__(self, repository: QueryLogRepository, audit: AuditService) -> None:
        self.repository, self.audit = repository, audit

    @staticmethod
    def view(row: Any, actor: CurrentUser | None = None) -> QueryView:
        payload = dict(row.payload)
        payload["details"] = {}
        payload["error"] = payload.get("error") if payload.get("error") in set(ErrorCode) else None
        payload["context_snapshot"] = redact(
            {
                key: value
                for key, value in payload.get("context_snapshot", {}).items()
                if key
                in {
                    "semantic",
                    "metrics",
                    "examples",
                    "prompt_versions",
                    "model_id",
                    "filters",
                    "time_range",
                    "retry_count",
                    "versions",
                    # Why a run ended where it did, and what an edited-SQL rerun started from.
                    "clarification",
                    "rerun_of",
                    "retries",
                    "warnings",
                    "interpretation",
                    # Why it failed: stage, guard rule, error type, evaluation case. No SQL.
                    "diagnostic",
                    "eval",
                }
            }
        )
        snapshot = payload["context_snapshot"]
        # Diagnostics are for admins and team leaders working out what went wrong.
        if actor is not None and actor.role == Role.MEMBER:
            snapshot.pop("diagnostic", None)
        if payload.get("status") == "blocked":
            payload.update(
                generated_sql=None,
                normalized_sql=None,
                # A refusal keeps only its diagnosis: which rule fired, never the SQL.
                context_snapshot={
                    key: snapshot[key] for key in ("diagnostic", "eval") if key in snapshot
                },
                result_preview=[],
                chart_spec={},
            )
        return QueryView.model_validate({**payload, "id": row.id, "created_at": row.created_at})

    async def list(self, actor: CurrentUser, filters: QueryFilter) -> Page[QueryView]:
        if not actor.active:
            raise PermissionDenied()
        if filters.since and filters.until and filters.since >= filters.until:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        rows, total = await self.repository.search(actor, filters)
        return Page(
            items=[self.view(row, actor) for row in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    async def get(self, identifier: int, actor: CurrentUser) -> QueryView:
        if not actor.active:
            raise PermissionDenied()
        row = await self.repository.get(identifier, actor)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return self.view(row, actor)

    async def write(
        self,
        actor: CurrentUser,
        datasource_id: int,
        status: str,
        metadata: QueryMetadata | None = None,
        sql: GuardedSql | None = None,
        result: QueryResult | None = None,
        blocked_code: ErrorCode | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> int:
        if not actor.active:
            raise PermissionDenied()
        if status not in {"success", "blocked", "failed", "clarify"}:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if status == "blocked" and (sql is not None or result is not None or blocked_code is None):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        metadata = metadata or QueryMetadata()
        async with self.repository.atomic():
            await QueryLogWriter(_EnrichedStore(self.repository, metadata, status)).write(
                actor,
                datasource_id,
                status,
                sql,
                result,
                blocked_code,
                metadata.context_snapshot.get("prompt_versions", {}),
                tokens_in,
                tokens_out,
            )
            if self.repository.last_id is None:
                raise AppError(ErrorCode.INTERNAL_ERROR)
            return self.repository.last_id

    async def mark_feedback(self, identifier: int, *, actor: CurrentUser) -> None:
        await self.get(identifier, actor)
        await self.repository.mark_feedback(identifier)
        await self.audit.record(actor, "querylog.feedback", ("query_run", str(identifier)))


def build_service(session: "AsyncSession") -> QueryLogService:
    return QueryLogService(QueryLogRepository(session), AuditService(DatabaseAuditStore(session)))
