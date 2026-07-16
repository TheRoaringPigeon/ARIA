import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import app.db as db_module
from app.dependencies import get_current_session
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


@pytest.fixture
def raw_client(mock_db):
    """A TestClient with no session override — only for exercising the
    real login/session mechanics themselves (see test_auth.py). Everything
    else should depend on `client` below.
    """
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
