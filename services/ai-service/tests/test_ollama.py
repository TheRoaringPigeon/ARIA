import json

import httpx
import pytest

import app.ollama as ollama_module
from app.config import settings
from app.ollama import chat_stream, complete, embed


class FakeStreamResponse:
    def __init__(self, lines, status_error=None):
        self._lines = lines
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamContextManager:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAsyncClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def stream(self, method, path, json=None):
        self.calls.append({"method": method, "path": path, "json": json})
        return FakeStreamContextManager(self._response)


async def test_chat_stream_yields_parsed_ndjson_lines_in_order(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "Hel"}, "done": False}),
        json.dumps({"message": {"content": "lo"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    fake_client = FakeAsyncClient(FakeStreamResponse(lines))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = [chunk async for chunk in chat_stream([{"role": "user", "content": "hi"}])]

    assert result == [json.loads(line) for line in lines]
    assert fake_client.calls[0]["method"] == "POST"
    assert fake_client.calls[0]["path"] == "/api/chat"
    assert fake_client.calls[0]["json"]["stream"] is True


async def test_chat_stream_skips_blank_lines(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "hi"}, "done": False}),
        "",
        "   ",
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    fake_client = FakeAsyncClient(FakeStreamResponse(lines))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = [chunk async for chunk in chat_stream([{"role": "user", "content": "hi"}])]

    assert result == [json.loads(lines[0]), json.loads(lines[3])]


async def test_chat_stream_raises_before_yielding_on_http_error(monkeypatch):
    fake_client = FakeAsyncClient(FakeStreamResponse([], status_error=httpx.HTTPError("boom")))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPError):
        async for _ in chat_stream([{"role": "user", "content": "hi"}]):
            pass


class FakeEmbedResponse:
    def __init__(self, embeddings):
        self._embeddings = embeddings

    def raise_for_status(self):
        pass

    def json(self):
        return {"embeddings": self._embeddings}


class FakeEmbedClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, path, json=None):
        self.calls.append({"path": path, "json": json})
        return self._response


async def test_embed_uses_embed_model_not_chat_model(monkeypatch):
    """`embed_model` and `ollama_model` are deliberately separate settings
    (a chat-tuned model makes unreliable embeddings for anything but
    near-exact text) — this locks in that `embed()` reads the right one.
    """
    fake_client = FakeEmbedClient(FakeEmbedResponse([[0.1, 0.2, 0.3]]))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(settings, "embed_model", "nomic-embed-text")
    monkeypatch.setattr(settings, "ollama_model", "qwen3:14b")

    result = await embed("some text")

    assert result == [0.1, 0.2, 0.3]
    assert fake_client.calls[0]["json"]["model"] == "nomic-embed-text"


async def test_embed_returns_empty_list_when_no_embeddings(monkeypatch):
    fake_client = FakeEmbedClient(FakeEmbedResponse([]))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    assert await embed("some text") == []


class FakeCompleteResponse:
    def __init__(self, content, status_error=None):
        self._content = content
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return {"message": {"role": "assistant", "content": self._content}}


class FakeCompleteClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, path, json=None):
        self.calls.append({"path": path, "json": json})
        return self._response


async def test_complete_returns_message_content_non_streaming(monkeypatch):
    fake_client = FakeCompleteClient(FakeCompleteResponse("vehicle"))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = await complete([{"role": "user", "content": "classify this"}])

    assert result == "vehicle"
    assert fake_client.calls[0]["path"] == "/api/chat"
    assert fake_client.calls[0]["json"]["stream"] is False


async def test_complete_raises_on_http_error(monkeypatch):
    fake_client = FakeCompleteClient(
        FakeCompleteResponse("", status_error=httpx.HTTPError("boom"))
    )
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPError):
        await complete([{"role": "user", "content": "hi"}])
