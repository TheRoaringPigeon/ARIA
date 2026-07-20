import httpx

import app.core_api_client as core_api_client_module
from app.core_api_client import (
    ENTITIES_FETCH_LIMIT,
    get_current_household_id,
    get_document,
    list_entities,
    list_entity_logs,
    list_entity_schedules,
)


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, result=None, status_code=200):
        self.result = result
        self.status_code = status_code
        self.calls = []

    async def get(self, path, params=None, cookies=None):
        self.calls.append({"path": path, "params": params, "cookies": cookies})
        return FakeResponse(self.result, self.status_code)


async def test_list_entities_forwards_cookie(monkeypatch):
    fake_client = FakeAsyncClient(result=[{"id": "1", "name": "Allen Woodward"}])
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    result = await list_entities("the-cookie-value")

    assert result == [{"id": "1", "name": "Allen Woodward"}]
    assert fake_client.calls[0]["path"] == "/entities"
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_list_entities_requests_core_apis_max_page_size(monkeypatch):
    fake_client = FakeAsyncClient(result=[])
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    await list_entities("the-cookie-value")

    assert fake_client.calls[0]["params"] == {"limit": ENTITIES_FETCH_LIMIT}


async def test_list_entity_logs_forwards_cookie_and_entity_id(monkeypatch):
    fake_client = FakeAsyncClient(result=[{"id": "log1"}])
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    result = await list_entity_logs("the-cookie-value", "entity123")

    assert result == [{"id": "log1"}]
    assert fake_client.calls[0]["path"] == "/entities/entity123/logs"
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_list_entity_schedules_forwards_cookie_and_entity_id(monkeypatch):
    fake_client = FakeAsyncClient(result=[{"id": "sched1"}])
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    result = await list_entity_schedules("the-cookie-value", "entity123")

    assert result == [{"id": "sched1"}]
    assert fake_client.calls[0]["path"] == "/entities/entity123/schedules"
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_get_document_forwards_cookie_and_document_id(monkeypatch):
    fake_client = FakeAsyncClient(result={"id": "doc1", "original_filename": "manual.pdf"})
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    result = await get_document("the-cookie-value", "doc1")

    assert result == {"id": "doc1", "original_filename": "manual.pdf"}
    assert fake_client.calls[0]["path"] == "/documents/doc1"
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_get_current_household_id_returns_id_on_success(monkeypatch):
    fake_client = FakeAsyncClient(result={"household_id": "h1", "user_id": "u1"})
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    result = await get_current_household_id("the-cookie-value")

    assert result == "h1"
    assert fake_client.calls[0]["path"] == "/auth/me"


async def test_get_current_household_id_degrades_to_none_on_expired_session(monkeypatch):
    fake_client = FakeAsyncClient(result=None, status_code=401)
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    assert await get_current_household_id("stale-cookie") is None


async def test_get_current_household_id_degrades_to_none_on_core_api_down(monkeypatch):
    class RaisingClient:
        async def get(self, path, params=None, cookies=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(core_api_client_module, "get_client", lambda: RaisingClient())

    assert await get_current_household_id("the-cookie-value") is None
