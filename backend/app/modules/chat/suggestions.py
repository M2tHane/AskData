"""Suggested questions: 3 starter questions per visible scope, cached, with a rule fallback."""

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.core.cache import TextCache
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.llm import CallContext, ChatMessage, extract_json_object
from app.core.permission import VisibleScope
from app.core.prompt import PromptId
from app.modules.chat.pipeline import PipelineDependencies
from app.modules.chat.schemas import RetrievalBundle, SuggestedQuestions

SUGGEST_COUNT = 3
MODEL_TTL_SECONDS = 24 * 3600
RULES_TTL_SECONDS = 10 * 60
MAX_TABLES = 20
MAX_COLUMNS = 15
MAX_METRICS = 10


Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class _ModelOutput(BaseModel):
    questions: list[Question] = Field(min_length=SUGGEST_COUNT, max_length=SUGGEST_COUNT)


def scope_summary(scope: VisibleScope, bundle: RetrievalBundle | None) -> dict[str, object]:
    """Names only, from the caller's visible scope; this is also the cache fingerprint input."""
    tables = {
        name: sorted(table.columns)[:MAX_COLUMNS]
        for name, table in sorted(scope.tables.items())[:MAX_TABLES]
    }
    metrics = (
        sorted({item.name for item in bundle.items if item.kind == "metric"}) if bundle else []
    )
    return {"tables": tables, "metrics": metrics[:MAX_METRICS]}


def fingerprint(summary: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:24]


def rule_questions(summary: dict[str, object]) -> list[str]:
    metrics = [str(m) for m in summary["metrics"]]  # type: ignore[attr-defined]
    tables = list(summary["tables"])  # type: ignore[call-overload]
    questions = [f"上个月的{name}是多少" for name in metrics[:2]]
    questions += [f"{str(name).split('.')[-1]} 表里有哪些数据" for name in tables]
    return questions[:SUGGEST_COUNT]


class SuggestionService:
    def __init__(self, deps: PipelineDependencies, cache: TextCache | None = None) -> None:
        self.deps, self.cache = deps, cache

    async def suggest(self, user: CurrentUser, datasource_id: int) -> SuggestedQuestions:
        scope = await self.deps.permissions.visible_scope(user, datasource_id)
        await self.deps.datasource(datasource_id, user)
        if not scope.tables:
            return SuggestedQuestions(questions=[], source="rules")
        bundle: RetrievalBundle | None = None
        if len(scope.tables) <= self.deps.builder.threshold:
            try:
                bundle = await self.deps.builder.build(user, scope, "")
            except AppError:
                bundle = None
        summary = scope_summary(scope, bundle)
        key = f"suggest:{datasource_id}:{fingerprint(summary)}"
        if self.cache is not None and (cached := await self.cache.get(key)):
            try:
                return SuggestedQuestions.model_validate_json(cached)
            except ValidationError:
                pass
        result = await self._from_model(user, summary)
        if result is None:
            result = SuggestedQuestions(questions=rule_questions(summary), source="rules")
        if self.cache is not None and result.questions:
            ttl = MODEL_TTL_SECONDS if result.source == "model" else RULES_TTL_SECONDS
            await self.cache.set(key, result.model_dump_json(), ttl)
        return result

    async def _from_model(
        self, user: CurrentUser, summary: dict[str, object]
    ) -> SuggestedQuestions | None:
        rendered = self.deps.prompts.render(
            PromptId.SUGGEST, summary, overrides=await self.deps.overrides()
        )
        try:
            response = await self.deps.llm.chat(
                [ChatMessage(role="system", content=rendered.content)],
                CallContext(
                    user_id=user.id,
                    team_id=user.team_id,
                    prompt_version=f"{PromptId.SUGGEST.value}:{rendered.version}",
                ),
            )
            output = _ModelOutput.model_validate_json(extract_json_object(response.text))
        except (AppError, ValidationError, ValueError):
            # Any model, quota or format problem degrades to the rules; the UI always has content.
            return None
        return SuggestedQuestions(questions=output.questions, source="model")
