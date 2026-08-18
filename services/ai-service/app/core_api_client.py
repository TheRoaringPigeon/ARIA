import logging

import httpx
from aria_auth import SESSION_COOKIE_NAME, SessionContext

from app.config import settings
from app.lazy_singleton import LazySingleton

logger = logging.getLogger(__name__)


class ChatAuthError(Exception):
    """Base for POST /chat's strict auth-gate failures (require_chat_session)."""


class ChatUnauthenticated(ChatAuthError):
    """No session cookie at all, or core-api's /auth/me rejected it with a
    401 (invalid/expired session) — chat.py maps this to HTTP 401.
    """


class ChatAuthUnavailable(ChatAuthError):
    """The auth check itself could not be completed — core-api unreachable/
    timed out, returned a non-401 error, or an unparseable /auth/me body.
    chat.py maps this to HTTP 503. Deliberately not the same as
    ChatUnauthenticated: the caller might be perfectly legitimate — it's
    core-api's own health that's unverifiable here, so /chat fails closed
    rather than silently proceeding ungrounded/unauthenticated (scaling-debt
    #11's explicit deviation from this module's usual degrade-to-None/[]
    stance elsewhere: /auth/me IS the primary auth check for /chat, not a
    secondary system sitting in front of one).
    """

# core-api's GET /entities caps `limit` at 200 (its own MAX_LIMIT) and
# defaults to only 50. Passing the max explicitly means entity-name
# matching sees as much of the household as core-api allows in one page —
# a household with more than 200 entities still gets a truncated grounding
# set (no pagination here yet; tracked as scaling-debt.md item #16, not #4).
ENTITIES_FETCH_LIMIT = 200

_client = LazySingleton(lambda: httpx.AsyncClient(base_url=settings.core_api_url, timeout=10.0))


def get_client() -> httpx.AsyncClient:
    return _client.get()


async def _get(path: str, cookie: str, params: dict | None = None) -> dict | list[dict]:
    resp = await get_client().get(path, params=params, cookies={SESSION_COOKIE_NAME: cookie})
    resp.raise_for_status()
    return resp.json()


async def list_entities(cookie: str) -> list[dict]:
    result = await _get("/entities", cookie, params={"limit": ENTITIES_FETCH_LIMIT})
    return result["items"]


async def list_entity_logs(cookie: str, entity_id: str, limit: int) -> list[dict]:
    result = await _get(f"/entities/{entity_id}/logs", cookie, params={"limit": limit})
    return result["items"]


async def list_entity_schedules(cookie: str, entity_id: str) -> list[dict]:
    return await _get(f"/entities/{entity_id}/schedules", cookie)


async def get_document(cookie: str, document_id: str) -> dict:
    return await _get(f"/documents/{document_id}", cookie)


async def get_household(cookie: str) -> dict | None:
    """The caller's own household record (`{id, name, city}`) — used to
    resolve chat's default weather location (M10) when a query doesn't
    name a place. Degrades to `None` on any failure, same
    401-vs-other-failure logging split used throughout this module, so
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


async def require_chat_session(cookie: str | None) -> SessionContext:
    """Hard authorization gate for POST /chat (scaling-debt.md #11). Unlike
    every other helper in this module (which degrades to `None`/`[]` on any
    auth failure so a secondary grounding pipeline can proceed ungrounded),
    this either returns a real, fully-populated session or raises —
    `chat.py` calls this once, at the very top of `chat()`, before any
    graph/Ollama work, for both the fresh-message and resume paths.

    - No cookie at all -> `ChatUnauthenticated` (no `/auth/me` call made at
      all — nothing to send).
    - `/auth/me` returns 401 -> `ChatUnauthenticated` (session invalid or
      expired).
    - Anything else `/auth/me` can fail with (timeout, connection error,
      5xx, or a body missing/null/empty on an expected field) ->
      `ChatAuthUnavailable`. core-api's own health is unverifiable in this
      branch, so `/chat` fails closed rather than degrading to
      ungrounded-but-served.

    Returns the full `/auth/me` payload in one round trip (not just
    `household_id`) so callers — specifically `rate_limit.check_chat_rate_limit`
    — can key off `user_id` without a second request.
    """
    if cookie is None:
        raise ChatUnauthenticated("no session cookie")
    try:
        response = await _get("/auth/me", cookie)
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            raise ChatUnauthenticated("session invalid or expired") from exc
        raise ChatAuthUnavailable(f"core-api /auth/me call failed: {exc}") from exc
    try:
        household_id = response["household_id"]
        user_id = response["user_id"]
        user_name = response["user_name"]
        role = response["role"]
    except (KeyError, TypeError) as exc:
        raise ChatAuthUnavailable(f"core-api /auth/me returned an unexpected shape: {exc}") from exc
    if not household_id or not user_id or not user_name or not role:
        raise ChatAuthUnavailable(
            f"core-api /auth/me returned an unexpected shape: missing/empty required field "
            f"(household_id={household_id!r}, user_id={user_id!r}, user_name={user_name!r}, role={role!r})"
        )
    return SessionContext(household_id=household_id, user_id=user_id, user_name=user_name, role=role)
