"""Runs the stages in order and turns any failure into a recorded, safe outcome."""

import asyncio
import contextlib
import time
from typing import Any

from pydantic import ValidationError

from app.core.errors import MESSAGES, AppError, ErrorCode, SqlBlocked
from app.core.llm import CallContext, query_call_scope
from app.core.logging import LogEvent, logger
from app.core.stream import EventType, StreamEvent
from app.modules.chat.dependencies import PipelineDependencies
from app.modules.chat.presentation import PresentationStage
from app.modules.chat.query_context import QueryContext, Stage
from app.modules.chat.stages import (
    ClarificationStage,
    CorrectionStage,
    ExecuteStage,
    GenerateSqlStage,
    GuardStage,
    LoadContextStage,
    RecordStage,
    RetrievalStage,
    SqlOutput,
    VisibleScopeStage,
)


def diagnose(ctx: QueryContext, error: BaseException) -> dict[str, Any]:
    """Why a question failed or was refused, in terms safe to keep and show to admins.

    Only the stage, the error code and type, the guard rule (plus a known SQL function name)
    and the evaluation case. Never SQL, data, exception messages or hidden object names.
    """
    facts: dict[str, Any] = {"stage": ctx.stage or "start", "error_type": type(error).__name__}
    if isinstance(error, AppError):
        facts["code"] = error.code.value
    if isinstance(error, SqlBlocked) and error.rule:
        facts["rule"] = error.rule.value
        if error.subject:
            facts["subject"] = error.subject
    elif isinstance(error, AppError) and (code := getattr(error, "subject", None)):
        facts["subject"] = code  # A standard database error code.
    elif isinstance(error, ValidationError) and error.errors():
        # The model's answer did not fit the schema: keep pydantic's error kind and the
        # schema field, both fixed names, never the model's text.
        first = error.errors()[0]
        facts["subject"] = str(first["type"])[:64]
        location = first["loc"]
        if location and location[0] in SqlOutput.model_fields:
            facts["field"] = location[0]
    if ctx.retries:
        facts["retries"] = len(ctx.retries)
        # What each earlier attempt hit, so a refusal after correction still shows the
        # first rule (a blocked log keeps only this diagnosis, not the retries list).
        facts["retry_rules"] = [
            entry.get("rule") or entry.get("subject") or entry["code"] for entry in ctx.retries
        ]
    if ctx.eval_ref:
        facts["eval_run_id"] = ctx.eval_ref.get("run_id")
        facts["eval_case_id"] = ctx.eval_ref.get("case_id")
    return facts


def log_diagnostic(ctx: QueryContext) -> None:
    if not ctx.diagnostic:
        return
    facts = ctx.diagnostic
    logger.info(
        LogEvent.DIAGNOSTIC,
        status=ctx.status,
        code=facts.get("code"),
        stage=facts.get("stage"),
        rule=facts.get("rule"),
        subject=facts.get("subject"),
        field=facts.get("field"),
        error_type=facts.get("error_type"),
        retries=facts.get("retries", 0),
        retry_rules="/".join(facts.get("retry_rules", [])) or None,
        eval_run_id=facts.get("eval_run_id"),
        eval_case_id=facts.get("eval_case_id"),
        query_run_id=ctx.query_run_id,
        datasource_id=ctx.datasource_id,
        user_id=ctx.user.id,
        model_id=ctx.model_id or None,
    )


class QueryPipeline:
    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps
        self.stages: list[Stage] = [
            LoadContextStage(deps),
            VisibleScopeStage(deps),
            RetrievalStage(deps),
            ClarificationStage(deps),
            GenerateSqlStage(deps),
            GuardStage(deps),
            ExecuteStage(deps),
            CorrectionStage(deps),
            PresentationStage(deps),
            RecordStage(deps),
        ]

    async def run(self, ctx: QueryContext) -> QueryContext:
        with query_call_scope(ctx.query_id):
            return await self._run(ctx)

    async def _run(self, ctx: QueryContext) -> QueryContext:
        cancelled = False
        try:
            await self.deps.llm.authorize_query(
                CallContext(
                    user_id=ctx.user.id,
                    team_id=ctx.user.team_id,
                    query_id=ctx.query_id,
                    prompt_version="",
                )
            )
            for stage in self.stages[:-1]:
                ctx.stage = stage.name
                started = time.monotonic()
                await ctx.event(EventType.STAGE, {"name": stage.name})
                try:
                    ctx = await stage.run(ctx)
                finally:
                    ctx.timings[stage.name] = int((time.monotonic() - started) * 1000)
                if ctx.status == "clarify":
                    break
        except asyncio.CancelledError as error:
            cancelled = True
            ctx.status, ctx.error_code = "failed", ErrorCode.QUERY_CANCELLED
            ctx.diagnostic = diagnose(ctx, error)
        except AppError as error:
            ctx.diagnostic = diagnose(ctx, error)
            ctx.error_code = error.code
            ctx.status = (
                "blocked"
                if isinstance(error, SqlBlocked)
                or error.code.value.startswith("SQL_")
                and error.code != ErrorCode.SQL_EXECUTION_ERROR
                else "failed"
            )
            ctx.explanation = MESSAGES[error.code]
        except ValidationError as error:
            # Usually model output that is not the JSON the prompt asked for.
            ctx.diagnostic = diagnose(ctx, error)
            ctx.status, ctx.error_code = "failed", ErrorCode.VALIDATION_ERROR
            ctx.explanation = MESSAGES[ErrorCode.VALIDATION_ERROR]
        except Exception as error:
            # Only the exception type: messages may carry SQL, data or secrets.
            logger.info(
                LogEvent.FAILURE, code=ErrorCode.INTERNAL_ERROR, action=type(error).__name__
            )
            ctx.diagnostic = diagnose(ctx, error)
            ctx.status, ctx.error_code = "failed", ErrorCode.INTERNAL_ERROR
            ctx.explanation = MESSAGES[ErrorCode.INTERNAL_ERROR]
        finally:
            # RecordStage always runs, including failures, clarification and disconnect.
            ctx.timings["record"] = 0
            if cancelled:
                await self._record_cancelled(ctx)
            else:
                await ctx.event(EventType.STAGE, {"name": self.stages[-1].name})
                await self.stages[-1].run(ctx)
        log_diagnostic(ctx)
        if ctx.error_code:
            await ctx.event(
                EventType.ERROR, {"code": ctx.error_code.value, "message": MESSAGES[ctx.error_code]}
            )
        await ctx.event(EventType.DONE, {"status": ctx.status, "query_run_id": ctx.query_run_id})
        if cancelled:
            raise AppError(ErrorCode.QUERY_CANCELLED, http_status=499)
        return ctx

    async def _record_cancelled(self, ctx: QueryContext) -> None:
        writer = self.deps.cancelled_querylog
        if writer is None:
            await asyncio.shield(self.stages[-1].run(ctx))
            return
        # The request session is being torn down; finish the log on its own session even if
        # this coroutine is cancelled again, and keep a reference until the write completes.
        task = asyncio.ensure_future(RecordStage(self.deps, writer).run(ctx))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        with contextlib.suppress(Exception):
            await asyncio.shield(task)


_BACKGROUND: set[asyncio.Future[QueryContext]] = set()


def encode_event(identifier: int, kind: EventType, data: dict[str, Any]) -> StreamEvent:
    return StreamEvent(id=identifier, event=kind, data=data)
