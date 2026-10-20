import pytest
from pydantic import SecretStr

from app.core.errors import AppError, ErrorCode
from app.core.llm import (
    CallContext,
    ChatMessage,
    EmbeddingClient,
    LLMClient,
    ModelConfig,
    ModelRegistry,
    RerankClient,
)
from tests.fakes import FakeModels, FakeTransport, FakeUsage


@pytest.fixture
def context():
    return CallContext(user_id=3, team_id=1, prompt_version="sql_generation:v1")


@pytest.fixture
def models():
    return [
        ModelConfig(
            id="primary",
            kind="llm",
            model="fake",
            api_key=SecretStr("secret"),
            retries=1,
            fallback_id="backup",
        ),
        ModelConfig(id="backup", kind="llm", model="fake", api_key=SecretStr("secret")),
        ModelConfig(id="embed", kind="embedding", model="fake", api_key=SecretStr("secret")),
        ModelConfig(id="rank", kind="rerank", model="fake", api_key=SecretStr("secret")),
    ]


async def test_chat_fallback_usage_and_template_versions(models, context):
    transport, usage = FakeTransport(), FakeUsage()
    transport.fail_models.add("primary")
    response = await LLMClient(ModelRegistry(FakeModels(models)), usage, transport).chat(
        [ChatMessage(role="user", content="How many orders?")],
        context,
    )
    assert response.model_id == "backup"
    assert transport.calls == ["primary", "primary", "backup"]
    assert len(usage.records) == 3
    assert usage.records[-1].usage.tokens_in == 10
    assert all(record.prompt_version == context.prompt_version for record in usage.records)
    assert "secret" not in repr(models)


async def test_stream_usage(models, context):
    usage = FakeUsage()
    chunks = [
        chunk
        async for chunk in LLMClient(
            ModelRegistry(FakeModels(models)),
            usage,
            FakeTransport(),
        ).stream([], context)
    ]
    assert "".join(chunk.text for chunk in chunks) == "SELECT id FROM orders"
    assert len(usage.records) == 1 and usage.records[0].usage.tokens_out == 3


async def test_partial_stream_is_never_replayed(models, context):
    transport, usage = FakeTransport(), FakeUsage()
    transport.fail_after_chunk = True
    chunks = []
    with pytest.raises(AppError):
        async for chunk in LLMClient(ModelRegistry(FakeModels(models)), usage, transport).stream(
            [],
            context,
        ):
            chunks.append(chunk)
    assert len(chunks) == 1 and transport.calls == ["primary"]
    assert len(usage.records) == 1 and not usage.records[0].success


async def test_embedding_rerank_and_disabled_team_selection(models, context):
    registry = ModelRegistry(FakeModels(models))
    assert await EmbeddingClient(registry, FakeUsage(), FakeTransport()).embed(["a", "b"], context)
    assert await RerankClient(registry, FakeUsage(), FakeTransport()).rerank(
        "x", ["a", "b"], context
    ) == [1, 0]
    registry.provider.preferred = "missing"
    with pytest.raises(AppError):
        await registry.select(1, "llm")


async def test_quota_denial_makes_no_provider_calls(models, context):
    transport, usage = FakeTransport(), FakeUsage()
    usage.block = AppError(ErrorCode.QUOTA_EXCEEDED)
    with pytest.raises(AppError) as caught:
        await LLMClient(ModelRegistry(FakeModels(models)), usage, transport).chat([], context)
    assert caught.value.code == ErrorCode.QUOTA_EXCEEDED and not transport.calls


async def test_exhausted_provider_errors_are_sanitized(models, context):
    transport = FakeTransport()
    transport.fail_models = {"primary", "backup"}
    with pytest.raises(AppError) as caught:
        await LLMClient(ModelRegistry(FakeModels(models)), FakeUsage(), transport).chat([], context)
    assert caught.value.code == ErrorCode.LLM_UNAVAILABLE
    assert "secret" not in caught.value.message


async def test_caller_selected_model_is_used_but_must_be_an_enabled_model_of_that_kind(
    models, context
):
    transport = FakeTransport()
    client = LLMClient(ModelRegistry(FakeModels(models)), FakeUsage(), transport)
    response = await client.chat([], context.model_copy(update={"requested_model_id": "backup"}))
    assert response.model_id == "backup" and transport.calls == ["backup"]
    # Wrong kind and unknown IDs never fall back to the default silently.
    for bad in ("embed", "missing"):
        with pytest.raises(AppError) as error:
            await client.chat([], context.model_copy(update={"requested_model_id": bad}))
        assert error.value.code == ErrorCode.LLM_UNAVAILABLE


# ---- streamed reasoning ----


def test_think_splitter_routes_inline_thinking_across_chunk_boundaries():
    from app.core.llm import ThinkSplitter

    splitter = ThinkSplitter()
    parts = [
        splitter.feed(chunk) for chunk in ["<thi", "nk>plan the", " join</th", 'ink>{"sql"', ":1}"]
    ]
    parts.append(splitter.flush())
    text = "".join(p[0] for p in parts)
    thought = "".join(p[1] for p in parts)
    assert text == '{"sql":1}' and thought == "plan the join"
    plain = ThinkSplitter()
    assert plain.feed("a < b") == ("a < b", "")
    # A possible tag start is held back until it can be decided, then released as text.
    assert plain.feed(" x <th") == (" x ", "") and plain.flush() == ("<th", "")


async def test_responses_stream_parses_reasoning_and_text_and_retries_without_summary(
    monkeypatch,
):
    import json as _json

    import httpx
    from pydantic import SecretStr

    from app.core.llm import LiteLLMTransport

    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        bodies.append(body)
        if body["reasoning"].get("summary"):
            return httpx.Response(400, text="summary unsupported")
        events = [
            {"type": "response.reasoning_text.delta", "delta": "think "},
            {"type": "response.reasoning_text.delta", "delta": "more"},
            {"type": "response.output_text.delta", "delta": '{"sql":'},
            {"type": "response.output_text.delta", "delta": '"SELECT 1"}'},
            {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 7, "output_tokens": 3}},
            },
        ]
        text = "".join(f"event: {e['type']}\ndata: {_json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    model = ModelConfig(
        id="r",
        kind="llm",
        model="openai/x",
        api_key=SecretStr("k"),
        base_url="http://gw/v1",
        api="responses",
        reasoning_effort="low",
    )
    chunks = [
        c async for c in LiteLLMTransport().stream(model, [ChatMessage(role="system", content="p")])
    ]
    assert "".join(c.reasoning for c in chunks) == "think more"
    assert "".join(c.text for c in chunks) == '{"sql":"SELECT 1"}'
    assert [c.usage for c in chunks if c.usage][0].tokens_in == 7
    assert bodies[0]["reasoning"] == {"effort": "low", "summary": "auto"} and bodies[0]["stream"]
    assert bodies[1]["reasoning"] == {"effort": "low"}
