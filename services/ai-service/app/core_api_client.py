import logging

import httpx
from aria_auth import SESSION_COOKIE_NAME

from app.config import settings

logger = logging.getLogger(__name__)

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


async def _get(path: str, cookie: str, params: dict | None = None) -> dict | list[dict]:
    resp = await get_client().get(path, params=params, cookies={SESSION_COOKIE_NAME: cookie})
    resp.raise_for_status()
    return resp.json()


async def list_entities(cookie: str) -> list[dict]:
    return await _get("/entities", cookie, params={"limit": ENTITIES_FETCH_LIMIT})


async def list_entity_logs(cookie: str, entity_id: str) -> list[dict]:
    return await _get(f"/entities/{entity_id}/logs", cookie)


async def list_entity_schedules(cookie: str, entity_id: str) -> list[dict]:
    return await _get(f"/entities/{entity_id}/schedules", cookie)


async def get_document(cookie: str, document_id: str) -> dict:
    return await _get(f"/documents/{document_id}", cookie)


async def get_current_household_id(cookie: str) -> str | None:
    """The one place `ai-service` ever learns the caller's literal
    `household_id` — every other read path just forwards the cookie to
    `core-api`, which derives `household_id` from the session server-side
    and never hands the raw value back. Chroma has no concept of "the
    caller's session," so retrieval needs the literal id to filter by (see
    `retrieval.py`). Degrades to `None` on any failure — no cookie, an
    expired/invalid session, or `core-api` unreachable — same
    401-vs-other-failure logging split as `entity_grounding.fetch_entities`,
    so retrieval can fall back to "no documents" rather than raising.
    """
    try:
        response = await _get("/auth/me", cookie)
        return response["household_id"]
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            logger.info("household id fetch skipped: session invalid or expired")
        else:
            logger.warning("household id fetch failed, degrading to ungrounded retrieval", exc_info=True)
        return None
