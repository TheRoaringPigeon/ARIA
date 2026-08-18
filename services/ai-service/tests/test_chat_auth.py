import httpx

import app.agents as agents_module
import app.core_api_client as core_api_client_module
import app.ollama as ollama_module
import app.redis_client as redis_client_module
import app.retrieval as retrieval_module
from app.config import settings
from app.core_api_client import require_chat_session as real_require_chat_session
from tests.conftest import _BrokenRedis
from tests.test_chat import make_fake_chat_stream, route_to


def _fail_if_graph_used(monkeypatch):
    """Proves the pipeline never runs on a rejected request — a 401/503
    must short-circuit before `agents.get_graph()` is ever awaited.
    """

    async def fail_if_called():
        raise AssertionError("agent graph should never be built for a rejected request")

    monkeypatch.setattr(agents_module, "get_graph", fail_if_called)


class _FakeUnauthorizedResponse:
    status_code = 401


def test_chat_returns_401_with_no_cookie(client, monkeypatch):
    monkeypatch.setattr(core_api_client_module, "require_chat_session", real_require_chat_session)
    _fail_if_graph_used(monkeypatch)

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "not authenticated"}


def test_chat_returns_401_on_invalid_session(client, monkeypatch):
    monkeypatch.setattr(core_api_client_module, "require_chat_session", real_require_chat_session)
    _fail_if_graph_used(monkeypatch)

    async def fake_get(path, cookie, params=None):
        assert path == "/auth/me"
        raise httpx.HTTPStatusError(
            "unauthorized", request=None, response=_FakeUnauthorizedResponse()
        )

    monkeypatch.setattr(core_api_client_module, "_get", fake_get)

    client.cookies.set("aria_session", "stale-cookie")
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "not authenticated"}


def test_chat_returns_503_when_core_api_unreachable(client, monkeypatch):
    monkeypatch.setattr(core_api_client_module, "require_chat_session", real_require_chat_session)
    _fail_if_graph_used(monkeypatch)

    async def fake_get(path, cookie, params=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(core_api_client_module, "_get", fake_get)

    client.cookies.set("aria_session", "a-cookie")
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert resp.status_code == 503
    assert resp.json() == {"detail": "authentication service unavailable"}


def test_chat_resume_also_requires_auth(client, monkeypatch):
    monkeypatch.setattr(core_api_client_module, "require_chat_session", real_require_chat_session)
    _fail_if_graph_used(monkeypatch)

    resp = client.post(
        "/chat",
        json={"messages": [], "resume": {"thread_id": "t1", "decision": "confirm"}},
    )

    assert resp.status_code == 401


async def _fake_retrieve_context(query, household_id):
    return []


def test_chat_rate_limits_after_threshold(client, monkeypatch):
    captured = {}
    route_to(monkeypatch, "general")
    monkeypatch.setattr(ollama_module, "chat_stream", make_fake_chat_stream(captured, "hi"))
    monkeypatch.setattr(retrieval_module, "retrieve_context", _fake_retrieve_context)

    for _ in range(settings.chat_rate_limit_requests):
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})
        assert resp.status_code == 200

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})

    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_chat_rate_limit_fails_open_when_redis_down(client, monkeypatch):
    captured = {}
    route_to(monkeypatch, "general")
    monkeypatch.setattr(ollama_module, "chat_stream", make_fake_chat_stream(captured, "hi"))
    monkeypatch.setattr(retrieval_module, "retrieve_context", _fake_retrieve_context)
    monkeypatch.setattr(redis_client_module, "_client", _BrokenRedis())

    for _ in range(settings.chat_rate_limit_requests + 5):
        resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})
        assert resp.status_code == 200
