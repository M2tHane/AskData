from collections.abc import Callable

from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, ChatMessage, LLMClient
from app.core.prompt import PromptId, PromptRegistry
from app.core.sql.dialect import Dialect, DialectAdapter
from app.core.sql.guard import SqlGuard
from app.modules.eval.assets import EvalAssets
from app.modules.eval.schemas import SAFETY_CATEGORIES, Evaluation, PipelineOutcome
from app.modules.eval.scoring import evaluate, summarize


class Gate1Runner:
    """Fixed simulated output goes through core.llm and the production SqlGuard.

    SQL execution is deliberately unavailable at this pre-pipeline milestone.
    An accepted candidate is a failed case, never a successful zero-execution block.
    """

    def __init__(
        self,
        assets: EvalAssets,
        clients: Callable[[str], LLMClient],
        guard: SqlGuard,
    ) -> None:
        self.assets, self.clients, self.guard = assets, clients, guard

    async def run(self) -> tuple[list[Evaluation], dict[str, object]]:
        results = []
        for case in self.assets.cases:
            if case.category not in SAFETY_CATEGORIES:
                continue
            if not case.candidate_sql:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            user = self.assets.users[case.as_user]
            datasource_id = self.assets.datasource_ids[case.datasource]
            scope = await self.assets.permissions.visible_scope(user, datasource_id)
            prompt = PromptRegistry().render(
                PromptId.SQL,
                {
                    "question": case.question,
                    "injection_context": case.injection_context,
                },
                dialect_hint=DialectAdapter(Dialect.POSTGRES).prompt_hint,
                engine=DialectAdapter(Dialect.POSTGRES).engine_name,
            )
            response = await self.clients(case.candidate_sql).chat(
                [ChatMessage(role="user", content=prompt.content)],
                CallContext(
                    user_id=user.id, team_id=user.team_id, prompt_version="sql_generation:v2"
                ),
            )
            try:
                self.guard.check(response.text, "postgres", scope)
                outcome = PipelineOutcome(status="failed", executed_queries=0)
            except AppError as error:
                outcome = PipelineOutcome(
                    status="blocked",
                    blocked_code=error.code,
                    executed_queries=0,
                    message=error.message,
                )
            results.append(
                evaluate(case, outcome, hidden_names=await self.assets.hidden_names(case))
            )
        return results, summarize(results)
