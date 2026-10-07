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
    vectors, used = await adapter.embed(model, ["a", "b"])
    assert vectors == [[0.1], [0.2]] and used.tokens_in == 10
    assert await adapter.rerank(model, "q", ["a", "b"]) == [1, 0]
