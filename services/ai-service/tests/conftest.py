import time

import pytest
from aria_auth import SessionContext
from fastapi.testclient import TestClient

import app.core_api_client as core_api_client_module
import app.redis_client as redis_client_module
from app.main import app

TEST_HOUSEHOLD_ID = "test-household"
TEST_USER_ID = "test-user"
TEST_USER_NAME = "Test User"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def fake_chat_auth(monkeypatch):
    """No real core-api `/auth/me` call in tests — every POST /chat request
    is authenticated by default via this fixed fake session, so the large
    majority of pre-existing `test_chat.py` tests (most of which never set a
    session cookie at all — they exist to exercise routing/retrieval/
    citation behavior, not auth) keep passing with zero per-test changes.
    Tests that exercise the auth gate itself restore the real
    `require_chat_session` per-test and mock `core_api_client._get`/
    `get_client` instead — see `test_chat_auth.py`/`test_core_api_client.py`.
    """

    async def fake_require_chat_session(cookie):
        return SessionContext(
            household_id=TEST_HOUSEHOLD_ID,
            user_id=TEST_USER_ID,
            user_name=TEST_USER_NAME,
            role="owner",
        )

    monkeypatch.setattr(core_api_client_module, "require_chat_session", fake_require_chat_session)


class _FakeRedis:
    """Hand-rolled in-memory fake supporting only the ops app/rate_limit.py
    calls (get/incr/expire/ttl) — no fakeredis/other test-double library,
    matching core-api's tests/conftest.py `_FakeRedis` (same shape, copied
    here since ai-service can't import a sibling service's test module).
    """

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._expires_at: dict[str, float] = {}

    def _expire_if_due(self, key: str) -> None:
        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= time.monotonic():
            self._counts.pop(key, None)
            self._expires_at.pop(key, None)

    async def get(self, key: str) -> str | None:
        self._expire_if_due(key)
        value = self._counts.get(key)
        return str(value) if value is not None else None

    async def incr(self, key: str) -> int:
        self._expire_if_due(key)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self._expires_at[key] = time.monotonic() + seconds
        return True

    async def ttl(self, key: str) -> int:
        self._expire_if_due(key)
        if key not in self._counts:
            return -2
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return -1
        return max(1, int(expires_at - time.monotonic()))


class _BrokenRedis:
    """Every call raises, simulating an unreachable Redis — for the
    fail-open test in `test_chat_auth.py`.
    """

    async def _raise(self, *args, **kwargs):
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("simulated redis outage")

    get = incr = expire = ttl = _raise


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """No real agent-store Redis db-1 connection in tests — every
    rate-limit check/increment goes against this in-memory fake. Fresh per
    test, so no counter state leaks between tests — existing tests posting
    to /chat 1-2 times per test stay well under the default threshold
    unmodified.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client_module, "_client", fake)
    return fake
