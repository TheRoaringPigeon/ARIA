from typing import NotRequired, TypedDict

from app import entity_grounding, retrieval
from app.schemas.chat import Citation

# Lives here (not `routers/chat.py`) specifically so `routers/chat.py` can
# import it without a circular dependency — `chat.py` already needs to
# import from this package (`get_graph`, `AGENT_LABELS`), and this constant
# is just as much "the general persona" as it is "the base prompt".
BASE_SYSTEM_PROMPT = (
    "You are ARIA, a household operations assistant. You help track homes, "
    "vehicles, equipment, and projects."
)


class AgentState(TypedDict):
    query: str
    selected_agent: NotRequired[str]
    persona: NotRequired[str]
    entity_context: NotRequired[list[entity_grounding.EntityContext]]
    chunks: NotRequired[list[retrieval.RetrievedChunk]]
    citation_list: NotRequired[list[Citation]]
    # Names of tools actually invoked this run — surfaced for debugging/
    # test assertions and available for a future `tool_call` SSE frame;
    # not consumed by `routers/chat.py` yet.
    tool_calls_made: NotRequired[list[str]]


# Single source of truth for which agent names the supervisor
# (`supervisor_node`, via `ModelAdapter.parse_choice()`) may ever return —
# `AGENT_LABELS` below must cover exactly this set. Lives here, not as a
# separately-maintained tuple in `nodes.py`, so the two can't silently
# desync.
VALID_AGENTS: tuple[str, ...] = ("maintenance", "vehicle", "research", "general")

# Single source of truth for both the `agent` SSE frame's display label and
# any future frontend display logic — the graph's `selected_agent` routing
# key on one side, a human-readable name on the other.
AGENT_LABELS: dict[str, str] = {
    "maintenance": "Maintenance Agent",
    "vehicle": "Vehicle Specialist",
    "research": "Research Assistant",
    "general": "ARIA",
}

# Fails at import time (test collection / app startup) rather than letting
# the two drift and only surfacing as a `KeyError` deep inside a live
# request's broad `except Exception` — where it would be silently
# misreported as "agent orchestration unavailable" instead of the code bug
# it actually is (caught in code review).
assert set(AGENT_LABELS) == set(VALID_AGENTS), (
    "AGENT_LABELS and VALID_AGENTS must name exactly the same set of agents"
)

MAINTENANCE_PERSONA = (
    BASE_SYSTEM_PROMPT + " Right now you are acting as the household's "
    "Maintenance Agent — focus on upkeep, service history, and recurring "
    "schedules across all tracked homes, vehicles, equipment, and projects."
)

VEHICLE_PERSONA = (
    BASE_SYSTEM_PROMPT + " Right now you are acting as the household's "
    "Vehicle Specialist — focus on vehicle-specific detail: mileage-based "
    "service intervals, makes/models/VINs, aftermarket parts, and "
    "vehicle maintenance history. Frame your answers with that expertise "
    "even when drawing on the same household records a generalist would."
)

RESEARCH_PERSONA = (
    BASE_SYSTEM_PROMPT + " Right now you are acting as the household's "
    "Research Assistant — focus on answering from the household's "
    "uploaded documents (manuals, receipts, invoices), and be explicit "
    "about citing the source excerpts you were given."
)

# Deliberately identical to `BASE_SYSTEM_PROMPT`, kept as a named alias so
# it's obvious "General" is meant to behave exactly like pre-M7 chat, not
# an oversight that it looks the same as the others minus specialization.
GENERAL_PERSONA = BASE_SYSTEM_PROMPT
