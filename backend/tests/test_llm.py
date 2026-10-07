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
