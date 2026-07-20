import httpx
import pytest

import app.core_api_client as core_api_client_module
from app.mcp_tools import create_log, create_schedule


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://core-api:8000/x")
            response = httpx.Response(self.status_code, json=self._json_data, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, result=None, status_code=200):
        self.result = result
        self.status_code = status_code
        self.calls = []

    async def post(self, path, json=None, cookies=None):
        self.calls.append({"path": path, "json": json, "cookies": cookies})
        return FakeResponse(self.result, self.status_code)


async def test_create_log_posts_args_and_forwards_cookie(monkeypatch):
    fake_client = FakeAsyncClient(result={"id": "log1", "title": "Oil change"})
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    args = {
        "entity_id": "e1",
        "type": "service",
        "occurred_at": "2026-07-18",
        "title": "Oil change",
    }
    result = await create_log("the-cookie-value", args)

    assert result == {"id": "log1", "title": "Oil change"}
    assert fake_client.calls[0]["path"] == "/logs"
    assert fake_client.calls[0]["json"] == args
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_create_log_raises_on_validation_error(monkeypatch):
    fake_client = FakeAsyncClient(
        result={"detail": "type 'expense' is not valid for domain 'vehicle'"}, status_code=400
    )
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await create_log("the-cookie-value", {"entity_id": "e1"})

    assert exc_info.value.response.status_code == 400
    assert (
        exc_info.value.response.json()["detail"]
        == "type 'expense' is not valid for domain 'vehicle'"
    )


async def test_create_schedule_posts_args_and_forwards_cookie(monkeypatch):
    fake_client = FakeAsyncClient(result={"id": "sched1", "title": "Rotate tires"})
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    args = {
        "entity_id": "e1",
        "title": "Rotate tires",
        "interval_type": "time",
        "interval_days": 180,
    }
    result = await create_schedule("the-cookie-value", args)

    assert result == {"id": "sched1", "title": "Rotate tires"}
    assert fake_client.calls[0]["path"] == "/schedules"
    assert fake_client.calls[0]["json"] == args
    assert fake_client.calls[0]["cookies"] == {"aria_session": "the-cookie-value"}


async def test_create_schedule_raises_on_validation_error(monkeypatch):
    fake_client = FakeAsyncClient(
        result={"detail": "interval_days is required when interval_type is 'time'"},
        status_code=400,
    )
    monkeypatch.setattr(core_api_client_module, "get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await create_schedule(
            "the-cookie-value", {"entity_id": "e1", "title": "x", "interval_type": "time"}
        )

    assert exc_info.value.response.status_code == 400
    assert (
        exc_info.value.response.json()["detail"]
        == "interval_days is required when interval_type is 'time'"
    )
