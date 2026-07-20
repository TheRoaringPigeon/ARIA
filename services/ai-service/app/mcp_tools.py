import logging

import httpx
from aria_auth import SESSION_COOKIE_NAME

from app import core_api_client

logger = logging.getLogger(__name__)

# Both wrap `core-api`'s existing POST /logs and POST /schedules exactly as
# they are today — permission checks, schedule validation, and due-date
# recompute logic all keep living in core-api. Live here (not
# `core_api_client.py`) since they're POSTs with an arbitrary write payload
# rather than today's fixed-path GETs, and are conceptually "the MCP tool
# surface" — even though they share `core_api_client.get_client()`'s
# underlying `httpx.AsyncClient` singleton, same as every read call does.


async def _post(path: str, session_cookie: str, args: dict) -> dict:
    resp = await core_api_client.get_client().post(
        path, json=args, cookies={SESSION_COOKIE_NAME: session_cookie}
    )
    resp.raise_for_status()
    return resp.json()


async def create_log(session_cookie: str, args: dict) -> dict:
    return await _post("/logs", session_cookie, args)


async def create_schedule(session_cookie: str, args: dict) -> dict:
    return await _post("/schedules", session_cookie, args)


def extract_error_detail(exc: httpx.HTTPStatusError) -> str:
    """core-api's write endpoints surface a validation failure two
    different ways depending on *where* it's caught, and both need to
    reach the caller rather than a generic failure message:

    - The handler's own `except ValidationError` (constructing the final
      `LogEntry`/`Schedule`) raises `HTTPException(400, str(exc))` —
      `{"detail": "<plain string>"}`.
    - `ScheduleCreate`'s own cross-field validator (e.g. "interval_days is
      required when interval_type is 'time'") runs during FastAPI's own
      request-body parsing, *before* the handler even runs — that's a 422
      with `{"detail": [{"msg": "...", ...}, ...]}`, FastAPI's own
      validation-error shape (a real, live-verified case, not a
      hypothetical one).

    Only ever returns text core-api deliberately put in a `detail` field —
    never the raw response body. Shared by both callers of `create_log`/
    `create_schedule` — `agents/nodes.py`'s `execute_action_node` hands this
    string straight to the model to paraphrase back to the household
    member, and `mcp_server.py`'s tool wrappers hand it straight to
    whatever external MCP client made the call — so an unhandled 500 (an
    uncaught exception, a Mongo failure) producing a body core-api never
    meant to be user-facing — Starlette's default plain-text error page, or
    worse — would otherwise leak internal error/implementation detail to
    either caller (caught in code review: this used to live only in
    `agents/nodes.py`, so `mcp_server.py`'s wrappers had no equivalent and
    let the raw httpx error message through instead). That case is logged
    server-side and reported to the caller as a generic failure instead.
    """
    generic = "core-api rejected the request"
    try:
        body = exc.response.json()
    except Exception:
        logger.warning("core-api returned a non-JSON error body: %r", exc.response.text)
        return generic
    if not isinstance(body, dict) or "detail" not in body:
        logger.warning("core-api returned an unrecognized error body: %r", body)
        return generic
    detail = body["detail"]
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [str(item["msg"]) for item in detail if isinstance(item, dict) and "msg" in item]
        if messages:
            # FastAPI/pydantic can report the same failing validator
            # more than once — de-dup while preserving order rather than
            # repeating the identical message back to the user.
            return "; ".join(dict.fromkeys(messages))
    logger.warning("core-api returned an unrecognized error detail shape: %r", detail)
    return generic
