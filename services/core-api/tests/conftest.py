import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import app.db as db_module
from app.main import app


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
def client(mock_db):
    # Entering as a context manager runs the app's lifespan (seeding), which
    # is why mock_db must be patched in before this fixture creates the app.
    with TestClient(app) as test_client:
        yield test_client
