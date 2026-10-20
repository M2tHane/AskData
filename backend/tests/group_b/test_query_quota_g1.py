import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.errors import AppError, ErrorCode
from app.core.llm import (
    CallContext,
    CallRecord,
    EmbeddingClient,
    LLMClient,
    ModelConfig,
    ModelRegistry,
    RerankClient,
    Usage,
    query_call_scope,
)
from app.modules.usage.models import TeamQuota, UsageDaily, UsageQuery
from app.modules.usage.schemas import QuotaInput
from tests.fakes import FakeModels, FakeTransport
from tests.group_b.test_usage import usage_service


def question(member, query_id=None):
    return CallContext(
        user_id=member.id,
        team_id=member.team_id,
        query_id=query_id or str(uuid4()),
        prompt_version="sql_generation:v1",
    )


async def test_one_question_multiple_models_fallbacks_and_retry_tokens(b_database, member, admin):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(daily_query_limit=1, monthly_token_limit=1000), actor=admin
        )
    registry = ModelRegistry(
        FakeModels(
            [
                ModelConfig(
                    id="primary",
                    kind="llm",
                    model="fake",
                    api_key=SecretStr("fake"),
                    retries=1,
                    fallback_id="backup",
                ),
                ModelConfig(id="backup", kind="llm", model="fake", api_key=SecretStr("fake")),
                ModelConfig(id="embed", kind="embedding", model="fake", api_key=SecretStr("fake")),
                ModelConfig(id="rank", kind="rerank", model="fake", api_key=SecretStr("fake")),
            ]
        )
    )
    transport = FakeTransport()
    transport.fail_models.add("primary")
    original = question(member)
    # Created before the scope: the core client must normalize this legacy context as well.
    legacy = CallContext(user_id=member.id, team_id=member.team_id, prompt_version="legacy:v1")
    async with b_database.session() as session:
        service = usage_service(b_database, session)
        await service.authorize_query(original)
        with query_call_scope(original.query_id):
            await EmbeddingClient(registry, service, transport).embed(["orders"], legacy)
            await RerankClient(registry, service, transport).rerank("orders", ["orders"], legacy)
            for stage in ("clarification", "sql_generation", "correction", "chart", "explanation"):
                await LLMClient(registry, service, transport).chat(
                    [], CallContext(user_id=member.id, team_id=1, prompt_version=f"{stage}:v1")
                )
        assert CallContext(user_id=3, prompt_version="background:v1").query_id is None
        assert len(transport.calls) == 17  # embed + rerank + five (two failures + fallback).
        assert await session.scalar(select(func.count()).select_from(UsageQuery)) == 1
        assert (await session.scalar(select(TeamQuota))).daily_calls == 1
        rows = list(await session.scalars(select(UsageDaily)))
        assert sum(row.calls for row in rows) == 17
        assert sum(row.tokens_in + row.tokens_out for row in rows) > 0
        with pytest.raises(AppError) as caught:
            await service.authorize_query(question(member))
        assert caught.value.code == ErrorCode.QUOTA_EXCEEDED and caught.value.http_status == 429
        assert caught.value.message
        # A denied second question doesn't prevent additional calls on the admitted first one.
        await service.authorize(original)
        await service.record(
            CallRecord(
                **original.model_dump(),
                model_id="backup",
                usage=Usage(tokens_out=2),
                duration_ms=1,
                success=True,
            )
        )


async def test_background_model_calls_use_tokens_without_daily_question_slots(
    b_database, member, admin
):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(daily_query_limit=0, monthly_token_limit=3), actor=admin
        )
    async with b_database.session() as session:
        service = usage_service(b_database, session)
        context = CallContext(user_id=member.id, team_id=1, prompt_version="embedding:v1")
        await service.authorize(context)
        await service.record(
            CallRecord(
                **context.model_dump(),
                model_id="embed",
                usage=Usage(tokens_in=3),
                duration_ms=1,
                success=True,
            )
        )
        assert await session.scalar(select(func.count()).select_from(UsageQuery)) == 0
        with pytest.raises(AppError) as caught:
            await service.authorize(context)
        assert caught.value.code == ErrorCode.QUOTA_EXCEEDED


async def test_same_query_concurrent_admissions_across_service_instances_count_once(
    b_database, member, admin
):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(daily_query_limit=1), actor=admin
        )
    context = question(member)

    async def admit():
        async with b_database.session() as session:
            await usage_service(b_database, session).authorize_query(context)

    await asyncio.gather(*(admit() for _ in range(4)))
    async with b_database.session() as session:
        assert (await session.scalar(select(TeamQuota))).daily_calls == 1
        assert await session.scalar(select(func.count()).select_from(UsageQuery)) == 1
        # A query ID cannot be reused by another person to avoid quota.
        other = context.model_copy(update={"user_id": 2})
        with pytest.raises(AppError) as caught:
            await usage_service(b_database, session).authorize_query(other)
        assert caught.value.code == ErrorCode.CONFLICT


async def test_utc_day_rollover_and_clarification_keeps_original_admission_date(
    b_database, member, admin, monkeypatch
):
    from app.modules.usage import repository as usage_repository

    day = datetime(2026, 1, 31, 23, 59, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return day

    monkeypatch.setattr(usage_repository, "datetime", Clock)
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(daily_query_limit=1), actor=admin
        )
    first, second = question(member), question(member)
    async with b_database.session() as session:
        await usage_service(b_database, session).authorize_query(first)
    day += timedelta(minutes=2)
    async with b_database.session() as session:
        service = usage_service(b_database, session)
        await service.authorize_query(first)  # Continuation consumes no second-day question slot.
        await service.authorize_query(second)
        with pytest.raises(AppError):
            await service.authorize_query(question(member))
        rows = list(await session.scalars(select(UsageQuery).order_by(UsageQuery.id)))
        assert [row.date.isoformat() for row in rows] == ["2026-01-31", "2026-02-01"]
        quota = await session.scalar(select(TeamQuota))
        assert quota.daily_date.isoformat() == "2026-02-01" and quota.daily_calls == 1


async def test_query_scope_is_task_local_and_restored_on_cancellation():
    started = asyncio.Event()
    values = []

    async def task(identifier):
        with query_call_scope(identifier):
            values.append(CallContext(user_id=3, prompt_version="sql:v1").query_id)
            started.set()
            await asyncio.Event().wait()

    with query_call_scope("outer"):
        pending = asyncio.create_task(task("inner"))
        await started.wait()
        assert CallContext(user_id=3, prompt_version="sql:v1").query_id == "outer"
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert CallContext(user_id=3, prompt_version="sql:v1").query_id == "outer"
    assert values == ["inner"]
    assert CallContext(user_id=3, prompt_version="sql:v1").query_id is None
