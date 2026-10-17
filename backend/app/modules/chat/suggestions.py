"""Suggested questions: 6 starter questions per visible scope, each tied to a visible metric or
table, cached, with a rule fallback."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.core.cache import TextCache
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.llm import CallContext, ChatMessage, extract_json_object
from app.core.permission import VisibleScope
from app.core.prompt import PromptId
from app.modules.chat.dependencies import PipelineDependencies
from app.modules.chat.schemas import RetrievalBundle, SuggestedQuestion, SuggestedQuestions

SUGGEST_COUNT = 6
MODEL_TTL_SECONDS = 24 * 3600
RULES_TTL_SECONDS = 10 * 60
MAX_TABLES = 20
MAX_COLUMNS = 15
MAX_METRICS = 10


Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class _ModelItem(BaseModel):
    question: Question
    kind: Literal["metric", "table"] | None = None
    name: str | None = None


class _ModelOutput(BaseModel):
    # Plain strings are still accepted so an admin's older custom prompt keeps working.
    questions: list[_ModelItem | Question] = Field(
        min_length=SUGGEST_COUNT, max_length=SUGGEST_COUNT + 2
    )


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


def short_table(name: object) -> str:
    return str(name).split(".")[-1]


def checked(item: _ModelItem | str, summary: dict[str, object]) -> SuggestedQuestion:
    """Keep the source only when it names a metric or table the caller can see."""
    if isinstance(item, str):
        return SuggestedQuestion(question=item)
    metrics = {str(m) for m in summary["metrics"]}  # type: ignore[attr-defined]
    tables = {short_table(t): short_table(t) for t in summary["tables"]}  # type: ignore[attr-defined]
    tables.update({str(t): short_table(t) for t in summary["tables"]})  # type: ignore[attr-defined]
    if item.kind == "metric" and item.name in metrics:
        return SuggestedQuestion(question=item.question, kind="metric", name=item.name)
    if item.kind == "table" and item.name in tables:
        return SuggestedQuestion(question=item.question, kind="table", name=tables[item.name])
    return SuggestedQuestion(question=item.question)


def rule_items(summary: dict[str, object]) -> list[SuggestedQuestion]:
    metrics = [str(m) for m in summary["metrics"]]  # type: ignore[attr-defined]
    tables = [short_table(t) for t in summary["tables"]]  # type: ignore[attr-defined]
    items = [
        SuggestedQuestion(question=f"上个月的{name}是多少", kind="metric", name=name)
        for name in metrics[:4]
    ]
    items += [
        SuggestedQuestion(question=f"{name} 表里有哪些数据", kind="table", name=name)
        for name in tables
    ]
    return items[:SUGGEST_COUNT]


def rule_questions(summary: dict[str, object]) -> list[str]:
    return [item.question for item in rule_items(summary)]


def packed(items: list[SuggestedQuestion], source: Literal["model", "rules"]) -> SuggestedQuestions:
    return SuggestedQuestions(
        questions=[item.question for item in items], items=items, source=source
    )


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
        # v2: six questions with sources; older cached entries are ignored.
        key = f"suggest:v2:{datasource_id}:{fingerprint(summary)}"
        if self.cache is not None and (cached := await self.cache.get(key)):
            try:
                return SuggestedQuestions.model_validate_json(cached)
            except ValidationError:
                pass
        result = await self._from_model(user, summary)
        if result is None:
            result = packed(rule_items(summary), "rules")
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
                    reasoning_effort="low",
                ),
            )
            output = _ModelOutput.model_validate_json(extract_json_object(response.text))
        except (AppError, ValidationError, ValueError):
            # Any model, quota or format problem degrades to the rules; the UI always has content.
            return None
        items = [checked(item, summary) for item in output.questions[:SUGGEST_COUNT]]
        return packed(items, "model")
