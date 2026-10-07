import asyncio
from uuid import uuid4

import pytest

from app.core.audit import AuditService, DatabaseAuditStore
from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, CallRecord, Usage
from app.core.response import PageParams
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import QuotaInput
from app.modules.usage.service import UsageService


def usage_service(database, session):
    return UsageService(
        UsageRepository(session, database.sessions), AuditService(DatabaseAuditStore(session))
    )


def context(member):
    return CallContext(
        user_id=member.id,
        team_id=member.team_id,
        prompt_version="sql_generation:v1",
        query_id=str(uuid4()),
    )


def record(member, tokens=3, success=True):
    return CallRecord(
        user_id=member.id,
        team_id=member.team_id,
        model_id="fake",
        prompt_version="sql_generation:v1",
        usage=Usage(tokens_in=tokens, tokens_out=1),
        duration_ms=1,
        success=success,
    )


async def test_quota_callback_durable_counts_and_role_filters(
    b_database, member, leader, admin, outsider
):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(monthly_token_limit=7, daily_query_limit=2), actor=admin
        )
    async with b_database.session() as session:
        service = usage_service(b_database, session)
        await service.authorize(context(member))
        await service.record(record(member, success=False))
        # Retries bill actual usage but don't reserve a second query slot.
        await service.record(record(member, tokens=1))
        rows = await service.list(member, PageParams())
        assert rows.items[0].calls == 2 and rows.items[0].tokens_in == 4
        assert (await service.list(leader, PageParams())).total == 1
        assert (await service.list(outsider, PageParams())).total == 0
        await service.authorize(context(member))
        with pytest.raises(AppError) as error:
            await service.authorize(context(member))
        assert error.value.code == ErrorCode.QUOTA_EXCEEDED
        await service.record(record(member))
        with pytest.raises(AppError):
            await service.authorize(context(member))
        with pytest.raises(AppError):
            await service.get_quota(1, outsider)
        with pytest.raises(AppError):
            await service.set_quota(1, QuotaInput(), actor=leader)


async def test_zero_monthly_quota_blocks_first_call(b_database, member, admin):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(monthly_token_limit=0), actor=admin
        )
    async with b_database.session() as session:
        with pytest.raises(AppError):
            await usage_service(b_database, session).authorize(context(member))


async def test_atomic_daily_reservation_allows_only_one_concurrent_call(b_database, member, admin):
    async with b_database.transaction() as session:
        await usage_service(b_database, session).set_quota(
            1, QuotaInput(daily_query_limit=1), actor=admin
        )

    async def reserve():
        async with b_database.session() as session:
            try:
                await usage_service(b_database, session).authorize(context(member))
                return "allowed"
            except AppError as error:
                return error.code

    results = await asyncio.gather(reserve(), reserve())
    assert results.count("allowed") == 1 and results.count(ErrorCode.QUOTA_EXCEEDED) == 1
