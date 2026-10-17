"""Collaborators shared by every stage, and the model call helpers they use."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder

from app.core.audit import QueryLogWriter
from app.core.datasource import Datasource
from app.core.deps import CurrentUser
from app.core.llm import CallContext, ChatMessage, LLMClient
from app.core.permission import PermissionService
from app.core.prompt import PromptId, PromptOverrides, PromptOverrideSource, PromptRegistry
from app.core.sql.dialect import DialectAdapter
from app.core.sql.executor import QueryLimits, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.core.stream import EventType
from app.modules.chat.context_builder import ContextBuilder
from app.modules.chat.query_context import MAX_REASONING_CHARS, QueryContext
from app.modules.chat.schemas import MessageView

MAX_CORRECTIONS = 3


# Prompts that only phrase an answer from a finished result; they need little reasoning.
LIGHT_PROMPTS: dict[PromptId, Literal["low", "medium", "high"]] = {PromptId.ANSWER: "low"}


class PipelineDependencies:
    def __init__(
        self,
        permissions: PermissionService,
        datasource: Callable[[int, CurrentUser], Awaitable[Datasource]],
        history: Callable[[int], Awaitable[list[MessageView]]],
        builder: ContextBuilder,
        llm: LLMClient,
        guard: SqlGuard,
        executor: SqlExecutor,
        querylog: QueryLogWriter,
        prompts: PromptRegistry | None = None,
        limits: QueryLimits | None = None,
        prompt_source: PromptOverrideSource | None = None,
        model_clarification: bool = False,
        cancelled_querylog: QueryLogWriter | None = None,
    ) -> None:
        self.permissions, self.datasource, self.history = permissions, datasource, history
        # Model-judged clarification is optional: fixed-output replays leave it off.
        self.model_clarification = model_clarification
        self.builder, self.llm, self.guard = builder, llm, guard
        self.executor, self.querylog = executor, querylog
        # Records runs cancelled by a disconnect without touching the request's session.
        self.cancelled_querylog = cancelled_querylog
        self.prompts = prompts or PromptRegistry()
        self.prompt_source = prompt_source
        self.limits = limits or QueryLimits(user_id=1)
        # Correction rounds per question; ablation runs set 0 to measure what they recover.
        self.max_corrections = MAX_CORRECTIONS

    def limits_for(self, user_id: int) -> QueryLimits:
        return self.limits.model_copy(update={"user_id": user_id})

    async def overrides(self) -> PromptOverrides | None:
        return await self.prompt_source.load() if self.prompt_source else None

    async def call(self, ctx: QueryContext, prompt_id: PromptId, data: dict[str, Any]) -> str:
        messages, context = await self._prepare(ctx, prompt_id, data)
        response = await self.llm.chat(messages, context)
        ctx.model_id = response.model_id
        ctx.tokens_in += response.usage.tokens_in
        ctx.tokens_out += response.usage.tokens_out
        return response.text

    async def stream_call(
        self,
        ctx: QueryContext,
        prompt_id: PromptId,
        data: dict[str, Any],
        text_event: EventType | None = None,
        show_reasoning: bool = True,
    ) -> str:
        """Stream one model call: thinking goes out as REASONING, text optionally as text_event.

        The answer call hides its thinking: once the conclusion streams, the thinking panel
        stays collapsed.

        Returns the complete text; callers parse and validate it exactly as with call().
        """
        messages, context = await self._prepare(ctx, prompt_id, data)
        parts: list[str] = []
        async for chunk in self.llm.stream(messages, context):
            if chunk.model_id:
                ctx.model_id = chunk.model_id
            if chunk.usage:
                ctx.tokens_in += chunk.usage.tokens_in
                ctx.tokens_out += chunk.usage.tokens_out
            if chunk.reasoning and show_reasoning:
                if len(ctx.reasoning) < MAX_REASONING_CHARS:
                    ctx.reasoning += chunk.reasoning
                await ctx.event(EventType.REASONING, {"delta": chunk.reasoning})
            if chunk.text:
                parts.append(chunk.text)
                if text_event is not None:
                    await ctx.event(text_event, {"delta": chunk.text})
        return "".join(parts)

    async def _prepare(
        self, ctx: QueryContext, prompt_id: PromptId, data: dict[str, Any]
    ) -> tuple[list[ChatMessage], CallContext]:
        adapter = DialectAdapter(ctx.datasource.dialect) if ctx.datasource else None
        overrides = await self.overrides()
        hint = adapter.prompt_hint if adapter else ""
        if adapter and overrides and adapter.prompt_name in overrides.dialects:
            published = overrides.dialects[adapter.prompt_name]
            hint = published.content
            ctx.prompt_versions[f"dialect_{adapter.prompt_name}"] = published.label
        rendered = self.prompts.render(
            prompt_id,
            jsonable_encoder(data),
            dialect_hint=hint,
            engine=adapter.engine_name if adapter else "",
            overrides=overrides,
        )
        ctx.prompt_versions[prompt_id.value] = rendered.version
        return [ChatMessage(role="system", content=rendered.content)], CallContext(
            user_id=ctx.user.id,
            team_id=ctx.user.team_id,
            prompt_version=f"{prompt_id.value}:{rendered.version}",
            requested_model_id=str(ctx.requested_model_id) if ctx.requested_model_id else None,
            reasoning_effort=LIGHT_PROMPTS.get(prompt_id),
        )

    def data(self, ctx: QueryContext) -> dict[str, Any]:
        return {
            "question": ctx.question,
            "history": [
                {
                    "role": item.role,
                    "content": item.content,
                    # Only guard-approved SQL survives VisibleScopeStage, so this is in scope.
                    **({"sql": item.normalized_sql} if item.normalized_sql else {}),
                }
                for item in ctx.history
            ],
            "context": ctx.retrieval.model_dump(mode="json"),
            "time_ranges": ctx.time_ranges,
            "inherited_sql": ctx.inherited_sql,
            "clarification_choice": ctx.clarification_choice,
            "clarification_ref": ctx.clarification_ref,
            # One clarification round per question; fixed-output replays never clarify.
            "clarification_allowed": self.model_clarification
            and not ctx.clarification_used
            and not ctx.manual_sql,
        }
