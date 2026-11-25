import json

import httpx
import pytest

import app.ollama as ollama_module
from app.config import settings
from app.ollama import (
    chat_stream,
    complete_structured,
    complete_structured_or_default,
    embed,
    estimate_tokens,
    fit_to_context,
)


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


async def test_chat_stream_sends_think_false_only_when_disabled(monkeypatch):
    lines = [json.dumps({"message": {"content": ""}, "done": True})]
    fake_client = FakeAsyncClient(FakeStreamResponse(lines))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    [chunk async for chunk in chat_stream([{"role": "user", "content": "hi"}])]
    [chunk async for chunk in chat_stream([{"role": "user", "content": "hi"}], think=False)]

    assert "think" not in fake_client.calls[0]["json"]
    assert fake_client.calls[1]["json"]["think"] is False


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
    monkeypatch.setattr(settings, "ollama_model", "qwen3.5:9b")

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


async def test_complete_structured_returns_parsed_dict(monkeypatch):
    schema = {"type": "object", "properties": {"agent": {"type": "string"}}}
    fake_client = FakeCompleteClient(FakeCompleteResponse(json.dumps({"agent": "vehicle"})))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = await complete_structured([{"role": "user", "content": "classify this"}], schema)

    assert result == {"agent": "vehicle"}
    call = fake_client.calls[0]
    assert call["path"] == "/api/chat"
    assert call["json"]["stream"] is False
    assert call["json"]["format"] == schema
    assert call["json"]["think"] is False
    assert call["json"]["options"] == {"temperature": 0, "num_ctx": settings.ollama_num_ctx}


async def test_complete_structured_raises_on_http_error(monkeypatch):
    fake_client = FakeCompleteClient(
        FakeCompleteResponse("", status_error=httpx.HTTPError("boom"))
    )
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPError):
        await complete_structured([{"role": "user", "content": "hi"}], {"type": "object"})


async def test_complete_structured_raises_on_malformed_json(monkeypatch):
    fake_client = FakeCompleteClient(FakeCompleteResponse("not json"))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    with pytest.raises(json.JSONDecodeError):
        await complete_structured([{"role": "user", "content": "hi"}], {"type": "object"})


async def test_complete_structured_or_default_returns_extracted_value_on_success(monkeypatch):
    fake_client = FakeCompleteClient(FakeCompleteResponse(json.dumps({"agent": "vehicle"})))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = await complete_structured_or_default(
        [{"role": "user", "content": "hi"}],
        {"type": "object"},
        extract=lambda decision: decision["agent"],
        default="general",
        warning="should not be logged",
    )

    assert result == "vehicle"


async def test_complete_structured_or_default_degrades_on_ollama_failure(monkeypatch):
    fake_client = FakeCompleteClient(
        FakeCompleteResponse("", status_error=httpx.HTTPError("boom"))
    )
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = await complete_structured_or_default(
        [{"role": "user", "content": "hi"}],
        {"type": "object"},
        extract=lambda decision: decision["agent"],
        default="general",
        warning="ollama unreachable",
    )

    assert result == "general"


async def test_complete_structured_or_default_degrades_on_non_dict_reply(monkeypatch):
    """`format`-constrained decoding only guarantees valid JSON, not that
    it matches the schema's top-level object shape — a schema-valid but
    non-dict reply (e.g. a bare list) makes `extract` raise, which must
    degrade to `default` the same as an unreachable Ollama, not escape
    this helper (caught in code review).
    """
    fake_client = FakeCompleteClient(FakeCompleteResponse(json.dumps(["not", "a", "dict"])))
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    result = await complete_structured_or_default(
        [{"role": "user", "content": "hi"}],
        {"type": "object"},
        extract=lambda decision: decision["agent"],
        default="general",
        warning="non-dict reply",
    )

    assert result == "general"


async def test_complete_structured_or_default_logs_the_given_warning(monkeypatch, caplog):
    fake_client = FakeCompleteClient(
        FakeCompleteResponse("", status_error=httpx.HTTPError("boom"))
    )
    monkeypatch.setattr(ollama_module, "get_client", lambda: fake_client)

    with caplog.at_level("WARNING"):
        await complete_structured_or_default(
            [{"role": "user", "content": "hi"}],
            {"type": "object"},
            extract=lambda decision: decision["agent"],
            default="general",
            warning="agent routing failed, defaulting to general",
        )

    assert "agent routing failed, defaulting to general" in caplog.text


def _turn(role, chars):
    return {"role": role, "content": "x" * chars}


def test_fit_to_context_keeps_a_prompt_that_fits(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 1000)
    messages = [_turn("system", 300), _turn("user", 300), _turn("assistant", 300)]
    assert fit_to_context(messages, reserve_tokens=100) == messages


def test_fit_to_context_drops_oldest_turns_but_keeps_system_and_last(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 1000)
    system, old, middle, last = (
        _turn("system", 900),
        {"role": "user", "content": "o" * 900},
        {"role": "assistant", "content": "m" * 900},
        _turn("user", 300),
    )

    fitted = fit_to_context([system, old, middle, last], reserve_tokens=100)

    assert fitted == [system, middle, last]
    assert estimate_tokens(fitted) <= 900


def test_fit_to_context_warns_when_nothing_is_left_to_drop(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ollama_num_ctx", 100)
    messages = [_turn("system", 3000), _turn("user", 3000)]

    assert fit_to_context(messages, reserve_tokens=10) == messages
    assert "exceeds" in caplog.text


async def test_chat_stream_sends_num_ctx_and_a_fitted_prompt(monkeypatch):
    captured = {}

    class Client:
        def stream(self, method, path, json=None):
            captured["json"] = json
            return FakeStream([])

    class FakeStream:
        def __init__(self, lines):
            self.response = FakeStreamResponse(lines)

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ollama_module, "get_client", lambda: Client())
    monkeypatch.setattr(settings, "ollama_num_ctx", 1000)
    monkeypatch.setattr(settings, "ollama_answer_reserve_tokens", 100)
    messages = [_turn("system", 300), _turn("user", 3000), _turn("user", 300)]

    [frame async for frame in chat_stream(messages)]

    assert captured["json"]["options"] == {"num_ctx": 1000}
    assert captured["json"]["messages"] == [messages[0], messages[2]]
