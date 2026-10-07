import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field, SecretStr

from app.core.errors import AppError, ErrorCode
from app.core.logging import LogEvent, logger


class ModelConfig(BaseModel):
    id: str
    kind: Literal["llm", "embedding", "rerank"]
    model: str
    api_key: SecretStr
    base_url: str | None = None
    enabled: bool = True
    timeout_seconds: float = Field(default=30, gt=0)
    retries: int = Field(default=2, ge=0, le=3)
    fallback_id: str | None = None


class ModelProvider(Protocol):
    async def configurations(self) -> list[ModelConfig]: ...
    async def preference(self, team_id: int | None, kind: str) -> str | None: ...
    async def default(self, kind: str) -> str | None: ...


class ModelRegistry:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    async def select(self, team_id: int | None, kind: str) -> ModelConfig:
        identifier = await self.provider.preference(team_id, kind) or await self.provider.default(
            kind
        )
        return await self.get(identifier, kind)

    async def get(self, identifier: str | None, kind: str) -> ModelConfig:
        for model in await self.provider.configurations():
            if model.id == identifier and model.kind == kind and model.enabled:
                return model
        raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(repr=False)


class Usage(BaseModel):
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)


class ChatResponse(BaseModel):
    text: str = Field(repr=False)
    usage: Usage = Field(default_factory=Usage)
    model_id: str = ""


class StreamChunk(BaseModel):
    text: str = Field(default="", repr=False)
    usage: Usage | None = None


_query_id: ContextVar[str | None] = ContextVar("llm_query_id", default=None)


@contextmanager
def query_call_scope(query_id: str) -> Iterator[None]:
    """Propagate a server-owned question ID to nested retrieval/model calls, task locally."""
    token = _query_id.set(query_id)
    try:
        yield
    finally:
        _query_id.reset(token)


class CallContext(BaseModel):
    user_id: int
    team_id: int | None = None
    prompt_version: str
    query_id: str | None = Field(
        default_factory=lambda: _query_id.get(), min_length=1, max_length=128
    )


def normalize_call_context(context: CallContext) -> CallContext:
    """Keep explicit IDs; fill contexts created before entering a question's scope."""
    return context.model_copy(update={"query_id": context.query_id or _query_id.get()})


class CallRecord(BaseModel):
    user_id: int
    team_id: int | None
    model_id: str
    prompt_version: str
    usage: Usage
    duration_ms: int
    success: bool
    query_id: str | None = Field(
        default_factory=lambda: _query_id.get(), min_length=1, max_length=128
    )


@runtime_checkable
class QueryQuotaRecorder(Protocol):
    async def authorize_query(self, context: CallContext) -> None:
        """Admit one question, including questions that need no model call, idempotently."""
        ...


class UsageRecorder(Protocol):
    async def authorize(self, context: CallContext) -> None:
        """Reserve quota atomically; reject excess via QUOTA_EXCEEDED."""
        ...

    async def record(self, record: CallRecord) -> None: ...


class ModelTransport(Protocol):
    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse: ...
    def stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]: ...
    async def embed(
        self, model: ModelConfig, texts: list[str]
    ) -> tuple[list[list[float]], Usage]: ...
    async def rerank(self, model: ModelConfig, query: str, documents: list[str]) -> list[int]: ...


class LiteLLMTransport:
    @staticmethod
    def _options(model: ModelConfig) -> dict[str, Any]:
        return {
            "model": model.model,
            "api_key": model.api_key.get_secret_value(),
            "api_base": model.base_url,
            "timeout": model.timeout_seconds,
            "num_retries": 0,
        }

    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse:
        from litellm import acompletion

        result: Any = await acompletion(
            **self._options(model), messages=[message.model_dump() for message in messages]
        )
        return ChatResponse(
            text=result.choices[0].message.content or "",
            model_id=model.id,
            usage=Usage(
                tokens_in=result.usage.prompt_tokens, tokens_out=result.usage.completion_tokens
            ),
        )

    async def stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        from litellm import acompletion

        result: Any = await acompletion(
            **self._options(model),
            stream=True,
            stream_options={"include_usage": True},
            messages=[message.model_dump() for message in messages],
        )
        try:
            async for chunk in result:
                usage = getattr(chunk, "usage", None)
                yield StreamChunk(
                    text=(chunk.choices[0].delta.content or "") if chunk.choices else "",
                    usage=Usage(tokens_in=usage.prompt_tokens, tokens_out=usage.completion_tokens)
                    if usage
                    else None,
                )
        finally:
            if hasattr(result, "aclose"):
                await result.aclose()

    async def embed(self, model: ModelConfig, texts: list[str]) -> tuple[list[list[float]], Usage]:
        from litellm import aembedding

        result: Any = await aembedding(**self._options(model), input=texts)
        ordered = sorted(result.data, key=lambda item: item["index"])
        return [item["embedding"] for item in ordered], Usage(tokens_in=result.usage.prompt_tokens)

    async def rerank(self, model: ModelConfig, query: str, documents: list[str]) -> list[int]:
        from litellm import arerank

        result: Any = await arerank(**self._options(model), query=query, documents=documents)
        return [item["index"] for item in result.results]


T = TypeVar("T")


class _Client:
    def __init__(
        self,
        registry: ModelRegistry,
        recorder: UsageRecorder,
        transport: ModelTransport | None = None,
    ) -> None:
        self.registry, self.recorder = registry, recorder
        self.transport = transport or LiteLLMTransport()

    async def authorize_query(self, context: CallContext) -> None:
        # Optional extension preserves existing recorders and fixed-model evaluation clients.
        if isinstance(self.recorder, QueryQuotaRecorder):
            await self.recorder.authorize_query(context)

    async def _invoke(
        self,
        kind: str,
        context: CallContext,
        operation: Callable[[ModelConfig], Awaitable[tuple[T, Usage]]],
    ) -> T:
        context = normalize_call_context(context)
        await self.recorder.authorize(context)
        model = await self.registry.select(context.team_id, kind)
        candidates = [model]
        if model.fallback_id:
            candidates.append(await self.registry.get(model.fallback_id, kind))
        for candidate in candidates:
            for _ in range(candidate.retries + 1):
                start = time.monotonic()
                usage, success = Usage(), False
                try:
                    async with asyncio.timeout(candidate.timeout_seconds):
                        result, usage = await operation(candidate)
                    success = True
                    return result
                except (asyncio.CancelledError, AppError):
                    raise
                except Exception:
                    pass
                finally:
                    await self._record(candidate, context, usage, start, success)
        raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)

    async def _record(
        self, model: ModelConfig, context: CallContext, usage: Usage, start: float, success: bool
    ) -> None:
        record = CallRecord(
            **context.model_dump(),
            model_id=model.id,
            usage=usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            success=success,
        )
        await self.recorder.record(record)
        logger.info(
            LogEvent.MODEL,
            model_id=model.id,
            prompt_version=context.prompt_version,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            duration_ms=record.duration_ms,
            status="success" if success else "failed",
        )


class LLMClient(_Client):
    async def chat(self, messages: list[ChatMessage], context: CallContext) -> ChatResponse:
        async def operation(model: ModelConfig) -> tuple[ChatResponse, Usage]:
            response = await self.transport.chat(model, messages)
            response.model_id = model.id
            return response, response.usage

        return await self._invoke("llm", context, operation)

    async def stream(
        self, messages: list[ChatMessage], context: CallContext
    ) -> AsyncIterator[StreamChunk]:
        context = normalize_call_context(context)
        await self.recorder.authorize(context)
        model = await self.registry.select(context.team_id, "llm")
        candidates = [model]
        if model.fallback_id:
            candidates.append(await self.registry.get(model.fallback_id, "llm"))
        for candidate in candidates:
            for _ in range(candidate.retries + 1):
                usage, success, emitted = Usage(), False, False
                start = time.monotonic()
                try:
                    async with asyncio.timeout(candidate.timeout_seconds):
                        async for chunk in self.transport.stream(candidate, messages):
                            if chunk.usage:
                                usage = chunk.usage
                            emitted = emitted or bool(chunk.text)
                            yield chunk
                    success = True
                    return
                except (asyncio.CancelledError, AppError):
                    raise
                except Exception:
                    if emitted:
                        raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503) from None
                finally:
                    await self._record(candidate, context, usage, start, success)
        raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)


class EmbeddingClient(_Client):
    async def embed(self, texts: list[str], context: CallContext) -> list[list[float]]:
        async def operation(model: ModelConfig) -> tuple[list[list[float]], Usage]:
            vectors, usage = await self.transport.embed(model, texts)
            if (
                len(vectors) != len(texts)
                or not vectors
                or any(len(vector) != len(vectors[0]) for vector in vectors)
            ):
                raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
            return vectors, usage

        return await self._invoke("embedding", context, operation)


class RerankClient(_Client):
    async def rerank(self, query: str, documents: list[str], context: CallContext) -> list[int]:
        async def operation(model: ModelConfig) -> tuple[list[int], Usage]:
            order = await self.transport.rerank(model, query, documents)
            if len(order) != len(set(order)) or any(
                index < 0 or index >= len(documents) for index in order
            ):
                raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
            return order, Usage()

        return await self._invoke("rerank", context, operation)
