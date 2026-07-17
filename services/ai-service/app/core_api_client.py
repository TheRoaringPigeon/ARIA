import httpx
from aria_auth import SESSION_COOKIE_NAME

from app.config import settings

_client: httpx.AsyncClient | None = None

# core-api's GET /entities caps `limit` at 200 (its own MAX_LIMIT) and
# defaults to only 100 with no explicit sort order. Passing the max
# explicitly means entity-name matching sees as much of the household as
# core-api allows in one page, instead of silently missing entities past
# an arbitrary, non-deterministic first-100 window.
ENTITIES_FETCH_LIMIT = 200


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.core_api_url, timeout=10.0)
    return _client


async def _get(path: str, cookie: str, params: dict | None = None) -> list[dict]:
    resp = await get_client().get(path, params=params, cookies={SESSION_COOKIE_NAME: cookie})
    resp.raise_for_status()
    return resp.json()


async def list_entities(cookie: str) -> list[dict]:
    return await _get("/entities", cookie, params={"limit": ENTITIES_FETCH_LIMIT})


async def list_entity_logs(cookie: str, entity_id: str) -> list[dict]:
    return await _get(f"/entities/{entity_id}/logs", cookie)


async def list_entity_schedules(cookie: str, entity_id: str) -> list[dict]:
    return await _get(f"/entities/{entity_id}/schedules", cookie)
