import logging

import httpx
from mcp.server.fastmcp import FastMCP

from app import mcp_tools
from app.config import settings
from aria_shared.schemas import LogCreate, ScheduleCreate

logger = logging.getLogger(__name__)

# Its own process (see the plan's design decisions) — not mounted into
# ai-service's existing FastAPI app. Host/port are `FastMCP` constructor
# kwargs in the installed SDK version, not `run()` kwargs (verified against
# mcp==1.28.1 before writing this).
mcp = FastMCP("aria-household-ops", host="0.0.0.0", port=settings.mcp_server_port)


@mcp.tool()
async def create_log(session_cookie: str, log: LogCreate) -> dict:
    """Log a completed maintenance item, expense, note, or other household
    event against a tracked entity (home, vehicle, equipment, project, or
    person). See the `log` argument's own field descriptions for details.
    """
    return await _call(
        mcp_tools.create_log, session_cookie, log.model_dump(mode="json", exclude_none=True)
    )


@mcp.tool()
async def create_schedule(session_cookie: str, schedule: ScheduleCreate) -> dict:
    """Create a recurring or one-off reminder/schedule against a tracked
    entity. See the `schedule` argument's own docstring and field
    descriptions for which fields each `interval_type` needs — those
    combinations are enforced by `ScheduleCreate`'s validator, not visible
    in the JSON schema itself.
    """
    return await _call(
        mcp_tools.create_schedule,
        session_cookie,
        schedule.model_dump(mode="json", exclude_none=True),
    )


async def _call(tool, session_cookie: str, args: dict) -> dict:
    """Same error-shaping `agents/nodes.py`'s `execute_action_node` applies
    to the in-process caller of `mcp_tools.create_log`/`create_schedule` —
    without this, an external MCP client hitting a validation error got
    FastMCP's generic wrapping of the raw httpx error message (internal
    hostnames/URLs, no actual validation reason) instead of the specific,
    safe detail core-api put in its response (caught in code review).
    FastMCP itself already turns any raised exception into a `ToolError`
    response rather than a crash/stack trace — raising here just improves
    the message it wraps.
    """
    try:
        return await tool(session_cookie, args)
    except httpx.HTTPStatusError as exc:
        logger.warning("MCP tool call rejected by core-api", exc_info=True)
        raise ValueError(mcp_tools.extract_error_detail(exc)) from exc


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
