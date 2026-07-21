import logging

import httpx
from aria_auth import SESSION_COOKIE_NAME

from app.config import settings
from app.lazy_singleton import LazySingleton

logger = logging.getLogger(__name__)

# core-api's GET /entities caps `limit` at 200 (its own MAX_LIMIT) and
# defaults to only 100 with no explicit sort order. Passing the max
# explicitly means entity-name matching sees as much of the household as
# core-api allows in one page, instead of silently missing entities past
# an arbitrary, non-deterministic first-100 window.
ENTITIES_FETCH_LIMIT = 200

_client = LazySingleton(lambda: httpx.AsyncClient(base_url=settings.core_api_url, timeout=10.0))


def get_client() -> httpx.AsyncClient:
    return _client.get()


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


async def get_household(cookie: str) -> dict | None:
    """The caller's own household record (`{id, name, city}`) — used to
    resolve chat's default weather location (M10) when a query doesn't
    name a place. Degrades to `None` on any failure, same
    401-vs-other-failure logging split as `get_current_household_id`, so
    the weather tool can fall back to "no default location" rather than
    raising.
    """
    try:
        result = await _get("/households/me", cookie)
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            logger.info("household fetch skipped: session invalid or expired")
        else:
            logger.warning("household fetch failed, degrading to no default location", exc_info=True)
        return None
    if not isinstance(result, dict):
        # A real (not merely theoretical) guard, unlike the `assert` this
        # replaced — `assert` is compiled out entirely under `python -O`/
        # `PYTHONOPTIMIZE`, which would have silently let a malformed
        # response propagate instead of degrading (caught in code review).
        logger.warning("household fetch returned unexpected shape %r, degrading to no default location", type(result).__name__)
        return None
    return result


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
