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
    # chat: chat/completions; responses: the OpenAI Responses protocol, where reasoning
    # text stays separate from the JSON answer content.
    api: Literal["chat", "responses"] = "chat"
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Embedding models that support it return vectors of this size (the index column's).
    dimensions: int | None = None


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
    # Input tokens the provider served from its prompt cache (a stable prompt prefix).
    tokens_cached: int = Field(default=0, ge=0)


def _cached_tokens(usage: Any) -> int:
    """Cached input tokens from chat (prompt_tokens_details) or responses (input_tokens_details)."""
    for name in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        value = (
            details.get("cached_tokens")
            if isinstance(details, dict)
            else getattr(details, "cached_tokens", None)
        )
        if isinstance(value, int) and value > 0:
            return value
    return 0


class ChatResponse(BaseModel):
    text: str = Field(repr=False)
    usage: Usage = Field(default_factory=Usage)
    model_id: str = ""


def extract_json_object(text: str) -> str:
    """Return the first complete JSON object, ignoring prose around it (e.g. reasoning).

    Extraction only: callers still validate against their schema and every generated
    SQL still passes the guard, so this never weakens any security boundary.
    """
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return text


class StreamChunk(BaseModel):
    text: str = Field(default="", repr=False)
    # Model thinking (reasoning summary or reasoning text), kept apart from the answer text.
    reasoning: str = Field(default="", repr=False)
    usage: Usage | None = None
    model_id: str = ""


class ThinkSplitter:
    """Route inline <think>...</think> blocks (some gateways inline reasoning) to reasoning.

    Streaming-safe: a tag split across chunks is held back until it can be decided.
    """

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.thinking, self.pending = False, ""

    def feed(self, text: str) -> tuple[str, str]:
        content: list[str] = []
        reasoning: list[str] = []
        buffer = self.pending + text
        self.pending = ""
        while buffer:
            tag = self._CLOSE if self.thinking else self._OPEN
            index = buffer.find(tag)
            if index == -1:
                # Hold back a suffix that could be the start of the tag.
                keep = next(
                    (
                        n
                        for n in range(min(len(tag) - 1, len(buffer)), 0, -1)
                        if tag.startswith(buffer[-n:])
                    ),
                    0,
                )
                emit, self.pending = buffer[: len(buffer) - keep], buffer[len(buffer) - keep :]
                (reasoning if self.thinking else content).append(emit)
                break
            (reasoning if self.thinking else content).append(buffer[:index])
            buffer = buffer[index + len(tag) :]
            self.thinking = not self.thinking
        return "".join(content), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        rest, self.pending = self.pending, ""
        return ("", rest) if self.thinking else (rest, "")


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
    # A caller-selected model; it must still be an enabled configuration of the right kind.
    requested_model_id: str | None = Field(default=None, min_length=1, max_length=64)
    # Upper bound on the model's reasoning effort for this call; light prompts (writing a
    # one-line answer) need not think as hard as SQL generation. Never raises it.
    reasoning_effort: Literal["low", "medium", "high"] | None = None


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
    def _chat_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
        # Prompts built from a single system message: chat gateways reject a request with
        # no user turn (400), so the prompt itself becomes the user message.
        if messages and all(message.role == "system" for message in messages):
            return [{"role": "user", "content": "\n\n".join(m.content for m in messages)}]
        return [message.model_dump() for message in messages]

    @staticmethod
    def _options(model: ModelConfig) -> dict[str, Any]:
        options = {
            "model": model.model,
            "api_key": model.api_key.get_secret_value(),
            "api_base": model.base_url,
            "timeout": model.timeout_seconds,
            "num_retries": 0,
        }
        if model.reasoning_effort:
            options["reasoning_effort"] = model.reasoning_effort
        return options

    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse:
        if model.api == "responses":
            return await self._responses_request(model, messages)
        from litellm import acompletion

        result: Any = await acompletion(
            **self._options(model), messages=self._chat_messages(messages)
        )
        return ChatResponse(
            text=result.choices[0].message.content or "",
            model_id=model.id,
            usage=Usage(
                tokens_in=result.usage.prompt_tokens,
                tokens_out=result.usage.completion_tokens,
                tokens_cached=_cached_tokens(result.usage),
            ),
        )

    async def _responses_request(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> ChatResponse:
        """Responses protocol: instructions carry the system role; reasoning stays separate."""
        import httpx

        body = self._responses_body(model, messages)
        try:
            async with httpx.AsyncClient(timeout=model.timeout_seconds) as client:
                response = await client.post(
                    f"{(model.base_url or '').rstrip('/')}/responses",
                    headers={"Authorization": f"Bearer {model.api_key.get_secret_value()}"},
                    json=body,
                )
        except httpx.HTTPError:
            # Message stays categorical; never leak endpoint URLs or response bodies.
            raise RuntimeError("responses transport unavailable") from None
        if response.status_code != 200:
            raise RuntimeError(f"responses api status {response.status_code}")
        data: Any = response.json()
        text = (
            "".join(
                part.get("text") or ""
                for item in data.get("output") or []
                if item.get("type") == "message"
                for part in item.get("content") or []
                if part.get("type") == "output_text"
            )
            or data.get("output_text")
            or ""
        )
        usage = data.get("usage") or {}
        return ChatResponse(
            text=text,
            model_id=model.id,
            usage=Usage(
                tokens_in=usage.get("input_tokens") or 0,
                tokens_out=usage.get("output_tokens") or 0,
                tokens_cached=_cached_tokens(usage),
            ),
        )

    @staticmethod
    def _responses_body(model: ModelConfig, messages: list[ChatMessage]) -> dict[str, Any]:
        instructions = "\n\n".join(
            message.content for message in messages if message.role == "system"
        )
        conversation = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role != "system"
        ]
        body: dict[str, Any] = {"model": model.model.removeprefix("openai/")}
        if conversation:
            body["input"] = conversation
            if instructions:
                body["instructions"] = instructions
        else:
            # Prompts built from a single system message: some gateways reject an empty
            # `input` (405), so the prompt itself becomes the user input.
            body["input"] = [{"role": "user", "content": instructions}]
        if model.reasoning_effort:
            body["reasoning"] = {"effort": model.reasoning_effort}
        return body

    async def _responses_stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        """Responses protocol over SSE: text and reasoning deltas arrive as typed events."""
        import json

        import httpx

        body = {**self._responses_body(model, messages), "stream": True}
        if model.reasoning_effort:
            # Ask for a readable reasoning summary; gateways that reject it get a plain retry.
            body["reasoning"] = {**body["reasoning"], "summary": "auto"}
        url = f"{(model.base_url or '').rstrip('/')}/responses"
        headers = {"Authorization": f"Bearer {model.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(timeout=model.timeout_seconds) as client:
                for attempt in range(2):
                    async with client.stream("POST", url, headers=headers, json=body) as response:
                        if response.status_code == 400 and attempt == 0 and model.reasoning_effort:
                            body["reasoning"] = {"effort": model.reasoning_effort}
                            continue
                        if response.status_code != 200:
                            raise RuntimeError(f"responses api status {response.status_code}")
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload or payload == "[DONE]":
                                continue
                            try:
                                event = json.loads(payload)
                            except ValueError:
                                continue
                            kind = event.get("type", "")
                            if kind == "response.output_text.delta":
                                yield StreamChunk(text=event.get("delta") or "")
                            elif kind in {
                                "response.reasoning_summary_text.delta",
                                "response.reasoning_text.delta",
                            }:
                                yield StreamChunk(reasoning=event.get("delta") or "")
                            elif kind == "response.reasoning_summary_part.done":
                                yield StreamChunk(reasoning="\n\n")
                            elif kind == "response.completed":
                                usage = (event.get("response") or {}).get("usage") or {}
                                yield StreamChunk(
                                    usage=Usage(
                                        tokens_in=usage.get("input_tokens") or 0,
                                        tokens_out=usage.get("output_tokens") or 0,
                                        tokens_cached=_cached_tokens(usage),
                                    )
                                )
                            elif kind in {"response.failed", "error"}:
                                raise RuntimeError("responses api stream failed")
                    return
        except httpx.HTTPError:
            raise RuntimeError("responses transport unavailable") from None

    async def stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        splitter = ThinkSplitter()
        if model.api == "responses":
            source = self._responses_stream(model, messages)
        else:
            source = self._chat_stream(model, messages)
        async for chunk in source:
            if chunk.text:
                text, thought = splitter.feed(chunk.text)
                chunk = chunk.model_copy(
                    update={"text": text, "reasoning": chunk.reasoning + thought}
                )
            yield chunk
        text, thought = splitter.flush()
        if text or thought:
            yield StreamChunk(text=text, reasoning=thought)

    async def _chat_stream(
        self, model: ModelConfig, messages: list[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        from litellm import acompletion

        result: Any = await acompletion(
            **self._options(model),
            stream=True,
            stream_options={"include_usage": True},
            messages=self._chat_messages(messages),
        )
        try:
            async for chunk in result:
                usage = getattr(chunk, "usage", None)
                delta = chunk.choices[0].delta if chunk.choices else None
                # Providers name the thinking field differently; LiteLLM normalizes most.
                reasoning = next(
                    (
                        value
                        for name in ("reasoning_content", "reasoning", "thinking")
                        if isinstance(value := getattr(delta, name, None), str) and value
                    ),
                    "",
                )
                yield StreamChunk(
                    text=(delta.content or "") if delta else "",
                    reasoning=reasoning,
                    usage=Usage(
                        tokens_in=usage.prompt_tokens,
                        tokens_out=usage.completion_tokens,
                        tokens_cached=_cached_tokens(usage),
                    )
                    if usage
                    else None,
                )
        finally:
            if hasattr(result, "aclose"):
                await result.aclose()

    async def embed(self, model: ModelConfig, texts: list[str]) -> tuple[list[list[float]], Usage]:
        from litellm import aembedding

        # Sent in the request body: LiteLLM rejects "dimensions" for model names it does not
        # know, while OpenAI-compatible providers (DashScope) honor it.
        extra: dict[str, Any] = (
            {"extra_body": {"dimensions": model.dimensions}} if model.dimensions else {}
        )
        result: Any = await aembedding(**self._options(model), input=texts, **extra)
        ordered = sorted(result.data, key=lambda item: item["index"])
        return [item["embedding"] for item in ordered], Usage(tokens_in=result.usage.prompt_tokens)

    async def rerank(self, model: ModelConfig, query: str, documents: list[str]) -> list[int]:
        if model.model.startswith("dashscope/"):
            return await self._dashscope_rerank(model, query, documents)
        from litellm import arerank

        result: Any = await arerank(**self._options(model), query=query, documents=documents)
        return [item["index"] for item in result.results]

    @staticmethod
    async def _dashscope_rerank(model: ModelConfig, query: str, documents: list[str]) -> list[int]:
        """DashScope's native rerank API; base_url is the bare host and the path is added here."""
        import httpx

        base = (model.base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        url = base if base.endswith(DASHSCOPE_RERANK_PATH) else base + DASHSCOPE_RERANK_PATH
        async with httpx.AsyncClient(timeout=model.timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {model.api_key.get_secret_value()}"},
                json={
                    "model": model.model.split("/", 1)[1],
                    "input": {"query": query, "documents": documents},
                    "parameters": {"return_documents": False, "top_n": len(documents)},
                },
            )
            response.raise_for_status()
            results = (response.json().get("output") or {}).get("results") or []
        ordered = sorted(results, key=lambda item: -float(item.get("relevance_score") or 0))
        return [int(item["index"]) for item in ordered]


T = TypeVar("T")

DASHSCOPE_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"

_EFFORT = {"low": 0, "medium": 1, "high": 2}


def capped(model: ModelConfig, context: CallContext) -> ModelConfig:
    """The model with its reasoning effort lowered to the call's bound, never raised."""
    if (
        context.reasoning_effort
        and model.reasoning_effort
        and _EFFORT[context.reasoning_effort] < _EFFORT[model.reasoning_effort]
    ):
        return model.model_copy(update={"reasoning_effort": context.reasoning_effort})
    return model


class _Client:
    # Throttled endpoints blackhole or reject bursts; never retry immediately.
    retry_backoff_seconds = 0.5
    retry_backoff_cap = 8.0

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

    async def _choose(self, context: CallContext, kind: str) -> ModelConfig:
        if context.requested_model_id:
            model = await self.registry.get(context.requested_model_id, kind)
        else:
            model = await self.registry.select(context.team_id, kind)
        return capped(model, context)

    async def _invoke(
        self,
        kind: str,
        context: CallContext,
        operation: Callable[[ModelConfig], Awaitable[tuple[T, Usage]]],
    ) -> T:
        context = normalize_call_context(context)
        await self.recorder.authorize(context)
        model = await self._choose(context, kind)
        candidates = [model]
        if model.fallback_id:
            candidates.append(capped(await self.registry.get(model.fallback_id, kind), context))
        for candidate in candidates:
            for attempt in range(candidate.retries + 1):
                if attempt:
                    await asyncio.sleep(
                        min(self.retry_backoff_seconds * 2**attempt, self.retry_backoff_cap)
                    )
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
            **context.model_dump(exclude={"requested_model_id", "reasoning_effort"}),
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
            tokens_cached=usage.tokens_cached,
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
        model = await self._choose(context, "llm")
        candidates = [model]
        if model.fallback_id:
            candidates.append(capped(await self.registry.get(model.fallback_id, "llm"), context))
        for candidate in candidates:
            for _ in range(candidate.retries + 1):
                usage, success, emitted = Usage(), False, False
                start = time.monotonic()
                try:
                    async with asyncio.timeout(candidate.timeout_seconds):
                        async for chunk in self.transport.stream(candidate, messages):
                            if chunk.usage:
                                usage = chunk.usage
                            # Once anything reached the caller, a retry would duplicate it.
                            emitted = emitted or bool(chunk.text or chunk.reasoning)
                            chunk.model_id = candidate.id
                            yield chunk
                    success = True
                    return
                except (asyncio.CancelledError, AppError):
                    raise
                except Exception as error:
                    # Only the type: provider messages can echo prompts or credentials.
                    logger.info(
                        LogEvent.FAILURE,
                        code=ErrorCode.LLM_UNAVAILABLE,
                        model_id=candidate.id,
                        action=type(error).__name__,
                    )
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
