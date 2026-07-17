import app.core_api_client as core_api_client_module
from app.core_api_client import (
    ENTITIES_FETCH_LIMIT,
    get_document,
    list_entities,
    list_entity_logs,
    list_entity_schedules,
)


class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def get(self, path, params=None, cookies=None):
        self.calls.append({"path": path, "params": params, "cookies": cookies})
        return FakeResponse(self.result)


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
