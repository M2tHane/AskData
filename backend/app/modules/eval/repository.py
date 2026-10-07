from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import BaseRepository
from app.core.errors import AppError, ErrorCode
from app.modules.eval.models import EvalCase, EvalResult, EvalRun
from app.modules.eval.schemas import EvalCaseInput, Evaluation


class EvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = BaseRepository(session, EvalRun)
        self.cases = BaseRepository(session, EvalCase)

    @asynccontextmanager
    async def transaction(self) -> Any:
        async with self.session.begin():
            yield

    @asynccontextmanager
    async def feedback_transaction(self) -> AsyncIterator[None]:
        # Feedback conversion owns the outer transaction. A savepoint isolates
        # validation/audit failures without committing its feedback update early.
        async with (
            self.session.begin_nested() if self.session.in_transaction() else self.session.begin()
        ):
            yield

    async def save_case(self, case: EvalCaseInput, actor_id: int) -> EvalCase:
        row = await self.session.scalar(select(EvalCase).where(EvalCase.external_id == case.id))
        values = {
            "external_id": case.id,
            "category": case.category,
            "difficulty": case.difficulty,
            "question": case.question,
            "expected_sql": case.expected_sql,
            "expected_result": case.expected_values,
            "expect_blocked": case.expect_status == "blocked",
            "expected_code": case.expect_code,
            "source": case.source,
            "definition": case.model_dump(mode="json"),
        }
        return (
            await self.cases.update(row, values)
            if row
            else await self.cases.create(values, actor_id)
        )

    async def all_cases(self) -> list[EvalCase]:
        return list(await self.session.scalars(select(EvalCase).order_by(EvalCase.external_id)))

    async def required_run(self, identifier: int, lock: bool = False) -> EvalRun:
        query = select(EvalRun).where(EvalRun.id == identifier)
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row

    async def save_result(self, run_id: int, case_id: int, result: Evaluation) -> None:
        self.session.add(
            EvalResult(
                run_id=run_id,
                case_id=case_id,
                passed=result.passed,
                actual_sql=result.actual_sql,
                actual_result_digest=result.actual_result_digest,
                blocked_code=result.blocked_code,
                detail=result.model_dump(mode="json"),
            )
        )
        await self.session.flush()

    async def results(self, run_id: int) -> list[Evaluation]:
        rows = await self.session.scalars(select(EvalResult).where(EvalResult.run_id == run_id))
        return [Evaluation.model_validate(row.detail) for row in rows]
