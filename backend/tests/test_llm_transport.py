import json
from types import SimpleNamespace

from pydantic import SecretStr

from app.core.llm import ChatMessage, LiteLLMTransport, ModelConfig


async def test_litellm_adapter_options_responses_usage_and_stream_close(monkeypatch):
    import litellm

    calls = []
    model = ModelConfig(
        id="adapter",
        kind="llm",
        model="openai/test",
        api_key=SecretStr("provider-key"),
        base_url="https://provider.invalid",
    )
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3)

    class Stream:
        closed = False

        def __aiter__(self):
            return self.body()

        async def body(self):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="sql"))], usage=None
            )
            yield SimpleNamespace(choices=[], usage=usage)

        async def aclose(self):
            self.closed = True

    stream = Stream()

    async def completion(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return stream
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="sql"))], usage=usage
        )

    async def embedding(**kwargs):
        return SimpleNamespace(
            data=[{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}], usage=usage
        )

    async def rerank(**kwargs):
        return SimpleNamespace(results=[{"index": 1}, {"index": 0}])

    monkeypatch.setattr(litellm, "acompletion", completion)
    monkeypatch.setattr(litellm, "aembedding", embedding)
    monkeypatch.setattr(litellm, "arerank", rerank)
    adapter = LiteLLMTransport()
    response = await adapter.chat(model, [ChatMessage(role="user", content="data")])
    assert response.text == "sql" and response.usage.tokens_in == 10
    assert calls[0]["api_key"] == "provider-key" and calls[0]["num_retries"] == 0
    chunks = [chunk async for chunk in adapter.stream(model, [])]
    assert chunks[0].text == "sql" and chunks[1].usage.tokens_out == 3 and stream.closed
    # A prompt made of one system message goes out as the user turn: chat gateways reject
    # requests without one.
    stream.closed = False
    _ = [chunk async for chunk in adapter.stream(model, [ChatMessage(role="system", content="p")])]
    assert calls[-1]["messages"] == [{"role": "user", "content": "p"}]
    vectors, used = await adapter.embed(model, ["a", "b"])
    assert vectors == [[0.1], [0.2]] and used.tokens_in == 10
    assert await adapter.rerank(model, "q", ["a", "b"]) == [1, 0]


async def test_responses_request_sends_system_prompt_as_user_input(monkeypatch):
    """Only-system prompts must produce a non-empty input; empty input gets rejected."""
    import httpx

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
                "usage": {"input_tokens": 7, "output_tokens": 2},
            },
        )

    real_client = httpx.AsyncClient

    def mocked_client(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    model = ModelConfig(
        id="reasoning",
        kind="llm",
        model="openai/zcode/GLM",
        api_key=SecretStr("key"),
        base_url="http://gateway/v1",
        api="responses",
    )
    response = await LiteLLMTransport().chat(
        model, [ChatMessage(role="system", content="系统提示词")]
    )
    body = captured["body"]
    assert body["model"] == "zcode/GLM" and "instructions" not in body
    assert body["input"] and body["input"][0]["role"] == "user"
    assert "系统提示词" in body["input"][0]["content"]
    assert response.text == "{}" and response.usage.tokens_in == 7


async def test_embedding_asks_for_the_index_dimension(monkeypatch):
    import litellm

    calls = []

    async def embedding(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            data=[{"index": 0, "embedding": [0.1] * 4}], usage=SimpleNamespace(prompt_tokens=3)
        )

    monkeypatch.setattr(litellm, "aembedding", embedding)
    model = ModelConfig(
        id="e", kind="embedding", model="openai/text-embedding", api_key=SecretStr("k")
    )
    await LiteLLMTransport().embed(model.model_copy(update={"dimensions": 1024}), ["订单"])
    await LiteLLMTransport().embed(model, ["订单"])
    assert calls[0]["dimensions"] == 1024 and "dimensions" not in calls[1]


async def test_dashscope_rerank_uses_the_native_path_on_a_bare_host(monkeypatch):
    import httpx

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 2, "relevance_score": 0.2},
                        {"index": 0, "relevance_score": 0.9},
                        {"index": 1, "relevance_score": 0.5},
                    ]
                }
            },
        )

    real_client = httpx.AsyncClient

    def mocked_client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    model = ModelConfig(
        id="r",
        kind="rerank",
        model="dashscope/qwen-rerank",
        api_key=SecretStr("rk"),
        base_url="https://dashscope.example.com/",
    )
    order = await LiteLLMTransport().rerank(model, "退款", ["a", "b", "c"])
    assert order == [0, 1, 2]
    assert captured["url"] == (
        "https://dashscope.example.com/api/v1/services/rerank/text-rerank/text-rerank"
    )
    assert captured["auth"] == "Bearer rk"
    body = captured["body"]
    assert body["model"] == "qwen-rerank"
    assert body["input"] == {"query": "退款", "documents": ["a", "b", "c"]}
