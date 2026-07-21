from typing import Literal, NotRequired, TypedDict

from app import entity_grounding, retrieval
from app.schemas.chat import Citation


class ProposedAction(TypedDict):
    """A create_log/create_schedule call `propose_action_node` wants to
    make, pending confirmation. `args` stays a bare `dict` (not modeled
    further here) — its shape is genuinely dynamic per `tool`, defined by
    `aria_shared.schemas.LogCreate`/`ScheduleCreate`, which is what
    actually validates it (at core-api, when the write is attempted).
    """

    tool: Literal["create_log", "create_schedule"]
    args: dict
    summary: str


class ActionResult(TypedDict):
    """What `execute_action_node` recorded for this turn. `summary` is set
    for `"done"`/`"cancelled"`, `detail` for `"failed"` — neither is set
    for `"unclear"`. See `routers/chat.py`'s `_render_action_result_note`,
    the one place all four `status` values are read.
    """

    status: Literal["done", "cancelled", "failed", "unclear"]
    summary: NotRequired[str]
    detail: NotRequired[str]

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
    # A handful of prior turns (`{"role", "content"}` dicts, oldest first)
    # for context — not the whole transcript. `query` alone is all any
    # node saw before this existed, which broke on a short follow-up like
    # "what about Warner Robins?" that only makes sense in light of the
    # previous turn: `supervisor_node` had nothing to classify against but
    # that one sentence, misrouted to `general`, and even a correctly
    # routed `research_node` would have had no idea what the follow-up was
    # asking about either (caught in code review, after live use surfaced
    # exactly this). See `routers/chat.py`'s `_routing_history`.
    history: NotRequired[list[dict]]
    selected_agent: NotRequired[str]
    persona: NotRequired[str]
    entity_context: NotRequired[list[entity_grounding.EntityContext]]
    chunks: NotRequired[list[retrieval.RetrievedChunk]]
    citation_list: NotRequired[list[Citation]]
    # Names of tools actually invoked this run — surfaced for debugging/
    # test assertions and available for a future `tool_call` SSE frame;
    # not consumed by `routers/chat.py` yet.
    tool_calls_made: NotRequired[list[str]]
    # M8 write path — set by `propose_action_node`/`execute_action_node`
    # (see app/agents/nodes.py). `proposed_action` is `None` when the model
    # couldn't confidently parse a create_log/create_schedule decision out
    # of the user's message. The confirm/reject decision itself never
    # lives in state — `execute_action_node` reads it straight off its own
    # `interrupt()` call's return value.
    proposed_action: NotRequired[ProposedAction | None]
    action_result: NotRequired[ActionResult | None]


# Single source of truth for which agent names the supervisor
# (`supervisor_node`, via `ModelAdapter.parse_choice()`) may ever return —
# `AGENT_LABELS` below must cover exactly this set. Lives here, not as a
# separately-maintained tuple in `nodes.py`, so the two can't silently
# desync.
VALID_AGENTS: tuple[str, ...] = ("maintenance", "vehicle", "research", "general", "action")

# Single source of truth for both the `agent` SSE frame's display label and
# any future frontend display logic — the graph's `selected_agent` routing
# key on one side, a human-readable name on the other.
AGENT_LABELS: dict[str, str] = {
    "maintenance": "Maintenance Agent",
    "vehicle": "Vehicle Specialist",
    "research": "Research Assistant",
    "general": "ARIA",
    # An action turn doesn't need its own specialist persona — the
    # interesting part is the proposed action, not a distinct voice.
    "action": "ARIA",
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
    "uploaded documents (manuals, receipts, invoices) and, when relevant, "
    "current information from the web or the weather, and be explicit "
    "about citing the source excerpts/results you were given."
)

# Deliberately identical to `BASE_SYSTEM_PROMPT`, kept as a named alias so
# it's obvious "General" is meant to behave exactly like pre-M7 chat, not
# an oversight that it looks the same as the others minus specialization.
GENERAL_PERSONA = BASE_SYSTEM_PROMPT

ACTION_PERSONA = (
    BASE_SYSTEM_PROMPT + " You can log completed maintenance/conversations and "
    "create reminders/schedules — but only ever through the confirm/cancel "
    "flow the household member already saw. Never claim an action happened "
    "unless the action result you were given says it did."
)
