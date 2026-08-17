import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mongomock_motor import AsyncMongoMockClient

import app.celery_client as celery_client_module
import app.db as db_module
import app.redis_client as redis_client_module
import app.s3 as s3_module
from app.config import settings
from app.dependencies import get_current_session, get_transaction_dep
from app.main import app
from aria_auth import SessionContext

TEST_HOUSEHOLD_ID = "test-household"
TEST_USER_ID = "test-user"
TEST_USER_NAME = "Test User"


@pytest.fixture
def mock_db(monkeypatch):
    """Point the app's Motor singleton at an in-memory mongomock client
    instead of a real Mongo instance. Patched at the module-global level
    (app.db._client) so both the lifespan's direct get_db() call and the
    get_db_dep() FastAPI dependency transparently pick it up.
    """
    mock_client = AsyncMongoMockClient()
    monkeypatch.setattr(db_module, "_client", mock_client)
    return mock_client[db_module.settings.mongo_db_name]


@pytest.fixture(autouse=True)
def mock_s3(monkeypatch):
    """Fake S3 for every test via moto — no real MinIO/network needed.
    Resets the module-level boto3 client singleton so it's (re)created
    inside moto's mock context; the app's default (unset) s3_endpoint_url
    is exactly what lets moto intercept the calls.
    """
    monkeypatch.setattr(s3_module, "_client", None)
    with mock_aws():
        yield


class _RecordingCelery:
    def __init__(self, calls: list[tuple[str, list]]):
        self._calls = calls

    def send_task(self, name: str, args: list) -> None:
        self._calls.append((name, args))


@pytest.fixture(autouse=True)
def mock_transaction():
    """mongomock's AsyncMongoMockClient can't do start_session() (confirmed
    directly: raises NotImplementedError) — override the transaction
    dependency with a no-op context manager so route business logic (right
    documents get the right updates) still runs end-to-end against
    mongomock. Real cross-write atomicity is verified live only, against a
    real replica-set Mongo — see
    docs/scaling-debt-plans/sd6a-entity-trash-cascade-transactions.md.
    """

    @asynccontextmanager
    async def _noop_transaction():
        yield None

    app.dependency_overrides[get_transaction_dep] = lambda: _noop_transaction
    yield
    app.dependency_overrides.pop(get_transaction_dep, None)


@pytest.fixture(autouse=True)
def celery_calls(monkeypatch):
    """No Redis/worker in tests — every fire-and-forget enqueue_* call
    (document processing, document deletion) is redirected here instead of
    attempting a real broker connection, matching the decoupling guarantee
    that CRUD writes don't depend on either being reachable. Tests can
    inspect this list to assert what got enqueued.
    """
    calls: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_client_module, "_get_celery", lambda: _RecordingCelery(calls))
    return calls


class _FakeRedis:
    """Hand-rolled in-memory fake supporting only the ops app/rate_limit.py
    calls (get/incr/expire/ttl) — no fakeredis/other test-double library,
    matching this repo's preference for small hand-rolled fakes (see
    _RecordingCelery above).
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
    fail-open tests in test_auth_rate_limit.py.
    """

    async def _raise(self, *args, **kwargs):
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("simulated redis outage")

    get = incr = expire = ttl = _raise


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """No real Redis in tests — every rate-limit check/increment goes
    against this in-memory fake instead. Fresh per test (function-scoped,
    like mock_db/celery_calls), so no counter state leaks between tests —
    existing tests calling login/signup 1-2 times per test stay well under
    the new default thresholds without any changes to them.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client_module, "_client", fake)
    return fake


@pytest.fixture
def raw_client(mock_db, monkeypatch):
    """A TestClient with no session override — only for exercising the
    real login/session mechanics themselves (see test_auth.py). Everything
    else should depend on `client` below.

    seed_admin defaults off (app/config.py) since a real deployment only
    needs it for its very first boot — but `lifespan` still needs to seed
    owner@household.local here so tests that log in as the seed user
    (test_auth.py, test_auth_signup.py) keep working against a fresh
    mongomock DB every run.
    """
    monkeypatch.setattr(settings, "seed_admin", True)
    with TestClient(app) as test_client:
        yield test_client


def _session_override(role: str):
    def _get_session() -> SessionContext:
        return SessionContext(
            household_id=TEST_HOUSEHOLD_ID,
            user_id=TEST_USER_ID,
            user_name=TEST_USER_NAME,
            role=role,
        )

    return _get_session


def set_session_role(role: str) -> None:
    """Switch the active `client` fixture's overridden session to a
    different role mid-test — e.g. to prove a permission that allows an
    owner action rejects the identical call from a member.
    """
    app.dependency_overrides[get_current_session] = _session_override(role)


@pytest.fixture
def client(raw_client):
    """A TestClient pre-authenticated as an `owner` via
    `app.dependency_overrides` — no real `/auth/login` round trip, no
    cookie, no session document in Mongo. Every route ultimately depends on
    the same `get_current_session` object (built once in
    `app/dependencies.py`), so overriding it here covers all of them. Call
    `set_session_role("member")` mid-test to switch roles on this client.
    """
    set_session_role("owner")
    yield raw_client
    app.dependency_overrides.pop(get_current_session, None)
