import httpx
import pytest

from app.mcp_server import _call


async def test_call_returns_tool_result_on_success():
    async def fake_tool(session_cookie, args):
        return {"id": "log1"}

    result = await _call(fake_tool, "cookie", {"entity_id": "e1"})

    assert result == {"id": "log1"}


async def test_call_translates_http_status_error_to_clean_message():
    request = httpx.Request("POST", "http://core-api:8000/logs")
    response = httpx.Response(
        400, json={"detail": "type 'expense' is not valid for domain 'vehicle'"}, request=request
    )

    async def failing_tool(session_cookie, args):
        raise httpx.HTTPStatusError("error", request=request, response=response)

    with pytest.raises(ValueError, match="type 'expense' is not valid for domain 'vehicle'"):
        await _call(failing_tool, "cookie", {"entity_id": "e1"})
