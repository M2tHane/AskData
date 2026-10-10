from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import delete, func, select
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

    async def find_run(self, identifier: int, lock: bool = False) -> EvalRun | None:
        query = select(EvalRun).where(EvalRun.id == identifier)
        if lock:
            # Sessions keep objects across commits; a locked read must see the current status.
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def required_run(self, identifier: int, lock: bool = False) -> EvalRun:
        row = await self.find_run(identifier, lock)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        return row

    async def scored_case_ids(self, run_id: int) -> set[int]:
        return set(
            await self.session.scalars(
                select(EvalResult.case_id).where(EvalResult.run_id == run_id)
            )
        )

    async def progress(self, run_ids: list[int]) -> dict[int, tuple[int, int, int]]:
        """(scored, passed, answer time ms) per run, counted live from saved answers."""
        if not run_ids:
            return {}
        rows = await self.session.execute(
            select(
                EvalResult.run_id,
                func.count(),
                func.count().filter(EvalResult.passed.is_(True)),
                func.coalesce(func.sum(EvalResult.detail["duration_ms"].as_integer()), 0),
            )
            .where(EvalResult.run_id.in_(run_ids))
            .group_by(EvalResult.run_id)
        )
        return {
            run_id: (int(done), int(passed), int(duration))
            for run_id, done, passed, duration in rows
        }

    async def delete_run(self, row: EvalRun) -> None:
        await self.session.execute(delete(EvalResult).where(EvalResult.run_id == row.id))
        await self.session.delete(row)
        await self.session.flush()

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

    async def runs_page(self, limit: int = 30) -> list[EvalRun]:
        return list(
            await self.session.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(limit))
        )

    async def result_count(self, run_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(EvalResult).where(EvalResult.run_id == run_id)
            )
            or 0
        )

    async def results(self, run_id: int) -> list[Evaluation]:
        rows = await self.session.scalars(select(EvalResult).where(EvalResult.run_id == run_id))
        return [Evaluation.model_validate(row.detail) for row in rows]
