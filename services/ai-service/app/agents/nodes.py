import asyncio
import logging
from datetime import date
from typing import get_args

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app import citations as citations_module
from app import entity_grounding, mcp_tools, ollama, retrieval
from app.adapters import get_adapter
from app.agents.state import (
    ACTION_PERSONA,
    GENERAL_PERSONA,
    MAINTENANCE_PERSONA,
    RESEARCH_PERSONA,
    VALID_AGENTS,
    VEHICLE_PERSONA,
    ActionResult,
    AgentState,
    ProposedAction,
)
from app.config import settings
from aria_shared.models.logs import LogType
from aria_shared.schemas import ScheduleCreate

logger = logging.getLogger(__name__)

_SUPERVISOR_SYSTEM_PROMPT = (
    "You are a routing classifier for a household operations assistant. "
    "Classify the user's question into exactly one category: "
    "'maintenance' (general upkeep, service history, or schedules across "
    "any tracked home, vehicle, equipment, or project), 'vehicle' "
    "(specifically about a car, truck, or other vehicle), 'research' "
    "(about the content of an uploaded document, manual, receipt, or "
    "invoice), 'log_or_schedule' (the user is asking you to record, log, "
    "or schedule something — not just asking a question), or 'general' "
    "(anything else, or if you're unsure). Respond with exactly one word: "
    "maintenance, vehicle, research, log_or_schedule, or general."
)

# The word `supervisor_node` asks the classifier model to output for each
# `VALID_AGENTS` entry — distinct from the internal agent name only for
# "action". `ModelAdapter.parse_choice()` (adapters/qwen.py) picks whichever
# candidate word appears *earliest* in the model's reply, and "action" is
# common enough in ordinary prose (e.g. "this doesn't need any action, it's
# a general question") to plausibly out-race the model's actual intended
# category — the other four category names already carry this same
# false-positive risk but are distinctive enough in practice; "action" was
# not (caught in code review).
_SUPERVISOR_CHOICE_WORDS: tuple[tuple[str, str], ...] = (
    ("maintenance", "maintenance"),
    ("vehicle", "vehicle"),
    ("research", "research"),
    ("action", "log_or_schedule"),
    ("general", "general"),
)
_AGENT_BY_CHOICE_WORD: dict[str, str] = {
    word: agent for agent, word in _SUPERVISOR_CHOICE_WORDS
}
# Same "fail at import time, not deep inside a broad `except`" reasoning as
# `AGENT_LABELS`/`VALID_AGENTS`'s own assertion in `agents/state.py`.
assert {agent for agent, _ in _SUPERVISOR_CHOICE_WORDS} == set(VALID_AGENTS), (
    "_SUPERVISOR_CHOICE_WORDS must name exactly the same set of agents as VALID_AGENTS"
)

# The enum/literal *values* are pulled straight from the schemas
# (LogCreate/ScheduleCreate's own source of truth, per their docstrings) so
# adding/removing a LogType or interval_type there takes effect here too
# without a matching edit — the rest of this prompt (which fields each
# interval_type needs) is still hand-written prose, since ScheduleCreate's
# validator logic doesn't reduce to a list worth deriving (caught in code
# review: this used to hardcode the enum values too, and could silently
# drift out of sync with the schema).
_LOG_TYPE_VALUES = ", ".join(get_args(LogType))
_SCHEDULE_INTERVAL_TYPES = ", ".join(
    get_args(ScheduleCreate.model_fields["interval_type"].annotation)
)

_ACTION_DECISION_SYSTEM_PROMPT = (
    "You are ARIA, a household operations assistant, deciding whether the "
    "user is asking you to log something that already happened or create a "
    "reminder/schedule for something recurring or upcoming.\n\n"
    "Respond with ONLY a JSON object, no other text: "
    '{"tool": "create_log"|"create_schedule"|null, "args": {...}, "summary": '
    '"<one-line plain-English description for a confirmation card>"}\n\n'
    "For create_log, args must include: entity_id (from the list you were "
    f"given), type (one of: {_LOG_TYPE_VALUES}), occurred_at (an ISO "
    "date, YYYY-MM-DD), title, and optionally description/cost/metrics.\n\n"
    "For create_schedule, args must include: entity_id (from the list you "
    f"were given), title, interval_type (one of: {_SCHEDULE_INTERVAL_TYPES}), "
    "plus whichever fields that interval_type needs — 'time' "
    "needs interval_days; 'usage' needs usage_metric and "
    "interval_usage_amount; 'once' needs planned_at (ISO date); 'monthly' "
    "needs either monthly_day, or monthly_weekday (0=Monday..6=Sunday) "
    "together with monthly_week_index (1-4, or -1 for 'last'). "
    "starting_at/starting_usage_value optionally seed the baseline.\n\n"
    "entity_id must be one of the ids from the household entities list you "
    "were given — never invent one. If you can't confidently identify both "
    "a specific entity from that list and the intended action, respond "
    'with {"tool": null}.'
)

_RESEARCH_TOOL_SYSTEM_PROMPT = (
    "You are the household's Research Assistant. You have one tool "
    "available: search_household_documents(query), which searches the "
    "household's uploaded documents (manuals, receipts, invoices) for "
    "relevant excerpts. Decide whether you need it to answer the user's "
    "question.\n\n"
    "Respond with ONLY a JSON object, no other text: "
    '{"tool": "search_household_documents", "query": "<search terms>"} '
    'to search, or {"tool": null} if you already have enough information '
    "or the question doesn't need a document search."
)


async def supervisor_node(state: AgentState, config: RunnableConfig) -> dict:
    """Classifies the query into one of `VALID_AGENTS` via a single
    non-streaming completion. Never uses Ollama's native `tools` param
    (see the plan's design decisions — it has a documented bug for Qwen3)
    — this is a plain instruction to respond with one word, parsed via
    `ModelAdapter.parse_choice()` rather than exact equality so "vehicle."
    or "I'd say vehicle" still route correctly. That parsing lives behind
    the adapter seam (not inline here) so a per-model quirk workaround
    stays swappable with the model, the same as `normalize_response()`/
    `create_stream_filter()` already are (caught in code review — this used
    to be a private `_classify()` helper hand-rolled directly in this
    file). Any failure to get or parse a usable answer degrades to
    "general" rather than failing the request — supervisor routing is
    additive, never load-bearing, same contract as every other grounding
    path in this codebase.

    Asks for (and matches against) `_SUPERVISOR_CHOICE_WORDS`'s classifier
    vocabulary, not `VALID_AGENTS` directly — see that mapping's own
    comment for why "action" needed a more distinctive stand-in word.
    """
    try:
        raw = await ollama.complete(
            [
                {"role": "system", "content": _SUPERVISOR_SYSTEM_PROMPT},
                {"role": "user", "content": state["query"]},
            ]
        )
        choice = get_adapter().parse_choice(raw, [word for _, word in _SUPERVISOR_CHOICE_WORDS])
        selected_agent = _AGENT_BY_CHOICE_WORD.get(choice, "general")
    except Exception:
        logger.warning("agent routing failed, defaulting to general", exc_info=True)
        selected_agent = "general"
    return {"selected_agent": selected_agent}


async def gather_baseline_context(
    query: str,
    cookie: str | None,
    entities: list[dict] | None = None,
    matched: list[dict] | None = None,
) -> tuple[list[entity_grounding.EntityContext], list[retrieval.RetrievedChunk], list]:
    """The "blanket gather" baseline used both as every non-research
    specialist's context (via `_gather_household_and_documents` below) and,
    in `routers/chat.py`'s `_route_and_gather`, as the pre-M7 M4/M5
    fallback when the orchestration layer itself is unavailable — one
    implementation so the two contracts can't silently drift apart (an
    earlier version duplicated this same `asyncio.gather` block in both
    places; caught in code review).

    Restores the pre-M7 concurrency shape: `retrieve_context()` runs first
    (citation resolution needs its output), then citation resolution and
    entity-context gathering run concurrently with each other — rather
    than resolving citations only after entity-context gathering has
    *also* finished, which added pure serial latency to every request
    (also caught in code review).

    `entities`/`matched`, if given, are forwarded to `gather_entity_context`
    to skip a redundant `GET /entities` fetch and/or a redundant
    word-boundary match — used by `propose_action_node`, which already
    fetched the household's entity list and matched it once for its own
    action-decision prompt (caught in code review: this used to redo both
    here, concurrently, on every action-classified turn).
    """
    # Passed as kwargs, and only when actually given, so the common
    # zero-extra-args call keeps the exact shape it always had — tests
    # (and any future caller) exercising the plain `(query, cookie)` case
    # don't need to know about the write path's extra parameters.
    extra: dict = {}
    if entities is not None:
        extra["entities"] = entities
    if matched is not None:
        extra["matched"] = matched
    entity_context_task = asyncio.create_task(
        entity_grounding.gather_entity_context(query, cookie, **extra)
    )
    try:
        chunks = await retrieval.retrieve_context(query)
        citation_list, entity_context = await asyncio.gather(
            citations_module.resolve_citations(cookie, chunks),
            entity_context_task,
        )
    except asyncio.CancelledError:
        # `entity_context_task` is a standalone task, not yet folded into
        # any `asyncio.gather`, while this coroutine is still suspended on
        # `retrieve_context()` above — cancelling *this* coroutine (e.g.
        # `propose_action_node`'s `baseline_task.cancel()`, once a
        # confident action decision makes this gather's result unneeded)
        # does not automatically cancel it too. Without this, it kept
        # running to completion in the background purely to have its
        # result discarded (caught in code review) — once inside the
        # `asyncio.gather` above, cancellation already cascades to it, so
        # this only matters for the earlier window.
        entity_context_task.cancel()
        try:
            await entity_context_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "entity_context_task raised after being cancelled alongside "
                "gather_baseline_context",
                exc_info=True,
            )
        raise
    return entity_context, chunks, citation_list


async def _gather_household_and_documents(
    state: AgentState, config: RunnableConfig, persona: str
) -> dict:
    """Shared body for `maintenance_node`/`vehicle_node`/`general_node` —
    all three always gather both entity context and document chunks (and
    resolve citations for those chunks), differing only in persona framing.

    An earlier version gave Maintenance/Vehicle *only* entity-grounding
    (no document search at all), on the theory that a curated,
    per-specialist tool subset was the more "agentic" design. Code review
    caught this as a real capability regression: pre-M7, every query
    always searched both — a vehicle-classified question about an
    uploaded owner's manual would now find nothing and cite nothing,
    something M4/M5 always handled. Restoring baseline parity here fixes
    that regression while still keeping genuine specialization: persona
    framing always differs, and `research_node` still layers a real
    bounded iterative search loop on top of this same baseline.
    """
    cookie = config["configurable"].get("cookie")
    entity_context, chunks, citation_list = await gather_baseline_context(
        state["query"], cookie
    )
    return {
        "entity_context": entity_context,
        "chunks": chunks,
        "citation_list": citation_list,
        "persona": persona,
        "tool_calls_made": ["gather_household_context", "search_household_documents"],
    }


async def maintenance_node(state: AgentState, config: RunnableConfig) -> dict:
    return await _gather_household_and_documents(state, config, MAINTENANCE_PERSONA)


async def vehicle_node(state: AgentState, config: RunnableConfig) -> dict:
    return await _gather_household_and_documents(state, config, VEHICLE_PERSONA)


async def general_node(state: AgentState, config: RunnableConfig) -> dict:
    """Fallback when the supervisor can't confidently classify — same
    baseline gather as every other specialist, `GENERAL_PERSONA` framing.
    """
    return await _gather_household_and_documents(state, config, GENERAL_PERSONA)


async def research_node(state: AgentState, config: RunnableConfig) -> dict:
    """Bounded tool-choice loop, capped at `settings.agent_max_tool_calls`
    iterations — the one specialist where genuine iterative tool use (not
    just a fixed context-gathering step) earns its keep: deciding whether
    to search, optionally reformulating the query, and whether to search
    again after seeing the first result. Any parse failure or exception
    just ends the loop early rather than raising — synthesis proceeds with
    whatever was gathered so far, same degrade-don't-fail contract as
    every other node.

    Also gathers baseline entity context, same as the other specialists
    (an earlier version didn't — code review caught that as a real
    regression: without it, `build_system_prompt()`'s citation-to-entity
    cross-referencing, e.g. "this document is linked to Dad", could never
    fire for a research-routed answer even when the cited document really
    is linked to a matched household entity). Kicked off as a concurrent
    task rather than awaited up front so it overlaps with the tool-choice
    loop's own LLM calls instead of adding sequential latency, and again
    with citation resolution once the loop finishes (see the dedup +
    citation-resolution step below).
    """
    query = state["query"]
    cookie = config["configurable"].get("cookie")
    entity_context_task = asyncio.create_task(
        entity_grounding.gather_entity_context(query, cookie)
    )

    chunks: list[retrieval.RetrievedChunk] = []
    tool_calls_made: list[str] = ["gather_household_context"]
    scratchpad: list[str] = []

    for _ in range(settings.agent_max_tool_calls):
        messages = [
            {"role": "system", "content": _RESEARCH_TOOL_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        if scratchpad:
            messages.append({"role": "assistant", "content": "\n".join(scratchpad)})

        try:
            # `parse_tool_decision` is inside the same try as the model
            # call, not after it — it used to sit outside this block, so a
            # `None` content from Ollama (a real quirk this codebase
            # already hit once, see `test_chat_treats_null_message_as_
            # empty_content`) would crash `normalize_response(None)`
            # uncaught, contradicting this function's own "never raises"
            # contract. Matches `supervisor_node`'s already-correct shape.
            raw = await ollama.complete(messages)
            decision = get_adapter().parse_tool_decision(raw)
        except Exception:
            logger.warning("research tool-decision call failed, stopping loop", exc_info=True)
            break

        tool = decision.get("tool")
        if not tool:
            break

        search_query = decision.get("query") or query
        new_chunks = await retrieval.retrieve_context(search_query)
        chunks.extend(new_chunks)
        tool_calls_made.append("search_household_documents")
        scratchpad.append(
            f'Already searched for "{search_query}" and found {len(new_chunks)} excerpt(s).'
        )

    # Two reformulated searches can legitimately return the same top chunk
    # (e.g. "water heater manual" vs. "water heater instructions" both
    # nearest-matching the same page) — dedup before it's rendered twice
    # into the prompt (caught in code review).
    chunks = retrieval.dedup_chunks(chunks)

    citation_list, entity_context = await asyncio.gather(
        citations_module.resolve_citations(cookie, chunks),
        entity_context_task,
    )
    return {
        "chunks": chunks,
        "entity_context": entity_context,
        "citation_list": citation_list,
        "persona": RESEARCH_PERSONA,
        "tool_calls_made": tool_calls_made,
    }


# --- M8: write path (propose -> execute) ------------------------------------
#
# Two single-purpose nodes, not one, specifically because a resumed
# LangGraph node re-runs its *entire function body* from the top rather than
# resuming mid-function (verified directly against the installed `langgraph`
# version before writing this — see the M8 plan's sequencing). A single node
# that made the LLM decision call and then called `interrupt()` would
# silently redo that LLM call on every confirm/cancel. Splitting the
# decision (`propose_action_node`, runs once) from the pause-then-write
# (`execute_action_node`, whose only work before its own `interrupt()` call
# is a cheap state read, so re-running it on resume costs nothing) sidesteps
# this entirely — an earlier version additionally split the pause into its
# own `confirm_gate_node`, but that bought nothing `execute_action_node`
# alone doesn't already provide, just a second graph hop and checkpoint
# write on every write-path turn (caught in code review).


def _render_matched_entities(entities: list[dict]) -> str:
    if not entities:
        return "(none matched)"
    return "\n".join(f"- {e['id']}: {e['name']} ({e['domain']})" for e in entities)


async def _fetch_entities_for_action(cookie: str | None) -> list[dict]:
    """The household's raw entity list, fetched once and shared between the
    action-decision prompt's matching (this function's caller, synchronous
    from here) and the concurrently-kicked-off `gather_baseline_context`
    (passed this same list) — an earlier version had each fetch and match
    independently, doubling `GET /entities` load on every action-classified
    turn (caught in code review). Delegates to
    `entity_grounding.fetch_entities` for the actual fetch-and-degrade
    (same 401-vs-other log-severity contract as the read path) rather than
    a second, separately-maintained copy of that try/except (also caught in
    code review — this used to log every failure, including a routine
    expired session, as a WARNING with a full traceback).
    """
    if cookie is None:
        return []
    return await entity_grounding.fetch_entities(cookie)


async def propose_action_node(state: AgentState, config: RunnableConfig) -> dict:
    """The one LLM decision call in the write path — which tool, which
    entity, what field values. Runs once, never re-entered (see the note
    above). Degrades to `proposed_action: None` on any failure (an
    unparseable reply, an entity the model named that isn't actually in the
    household's matched list, an unreachable core-api) rather than raising —
    same contract as every other node in this graph.

    Also gathers the same baseline context every other specialist gathers
    (`gather_baseline_context`), concurrently with the decision call so it
    doesn't add latency — but only surfaces it in state when
    `proposed_action` ends up `None`, so a "couldn't tell what you meant"
    turn still has grounding to answer a clarifying question from (the same
    never-answer-from-nothing standard every other specialist already
    meets).
    """
    query = state["query"]
    cookie = config["configurable"].get("cookie")
    entities = await _fetch_entities_for_action(cookie)
    # `uncapped=True` — unlike the read-path grounding prompt, a write-path
    # whitelist must not silently drop a genuine match past
    # `settings.entity_match_limit`: an entity beyond that cap would be
    # both invisible to the model (not in the rendered list below) and
    # unselectable (not in `matched_ids` below), degrading a well-formed
    # request to "couldn't tell what you meant" for no good reason (caught
    # in code review).
    matched = entity_grounding.find_matching_entities(query, entities, uncapped=True)
    # The *fallback* grounding call below is the read-path contract, not
    # the write-path whitelist — it must stay capped at
    # `settings.entity_match_limit` like every other specialist's baseline
    # gather, or a broad query term matching many entities turns a plain
    # clarifying-question turn into an unbounded core-api fan-out (caught
    # in code review: this used to pass the uncapped `matched` list
    # straight through).
    baseline_matched = matched[: settings.entity_match_limit]
    baseline_task = asyncio.create_task(
        gather_baseline_context(query, cookie, entities, baseline_matched)
    )

    proposed_action: ProposedAction | None = None
    try:
        user_content = (
            f"Today's date is {date.today().isoformat()}.\n\n"
            f"Household entities you can reference:\n{_render_matched_entities(matched)}\n\n"
            f"User message: {query}"
        )
        raw = await ollama.complete(
            [
                {"role": "system", "content": _ACTION_DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        decision = get_adapter().parse_tool_decision(raw)
        tool = decision.get("tool")
        args = decision.get("args")
        summary = decision.get("summary")
        matched_ids = {e["id"] for e in matched}
        if (
            tool in ("create_log", "create_schedule")
            and isinstance(args, dict)
            and isinstance(summary, str)
            and summary
            and args.get("entity_id") in matched_ids
        ):
            proposed_action = {"tool": tool, "args": args, "summary": summary}
    except Exception:
        logger.warning("action-decision call failed, degrading to no proposed action", exc_info=True)

    if proposed_action is not None:
        baseline_task.cancel()
        try:
            await baseline_task
        except asyncio.CancelledError:
            pass
        except Exception:
            # `baseline_task` may have already raised (or been mid-raise)
            # before the cancellation was honored — surfaced through this
            # file's usual logging convention rather than left for
            # asyncio's default handler to log as an untracked "Task
            # exception was never retrieved" with no context (caught in
            # code review). Its result is discarded either way: a
            # confident decision has no use for the baseline gather.
            logger.warning(
                "gather_baseline_context raised after being cancelled by a "
                "confident action decision", exc_info=True
            )
        # Set even though this turn never renders a system prompt itself
        # (the interrupted turn emits only `action_proposed`, no Ollama
        # call) — it carries forward through the checkpointer so the
        # *resumed* turn's acknowledgment answer gets ACTION_PERSONA's
        # "never claim an action happened unless the result says it did"
        # guardrail, instead of falling back to GENERAL_PERSONA.
        return {"proposed_action": proposed_action, "persona": ACTION_PERSONA}

    entity_context, chunks, citation_list = await baseline_task
    return {
        "proposed_action": None,
        "entity_context": entity_context,
        "chunks": chunks,
        "citation_list": citation_list,
        "persona": ACTION_PERSONA,
        "tool_calls_made": ["gather_household_context", "search_household_documents"],
    }


async def execute_action_node(state: AgentState, config: RunnableConfig) -> dict:
    """Pauses for confirmation (via `interrupt()`) and then performs the
    write, records a cancellation, or records "couldn't determine a
    specific action" — never raises out of this node; a failed write
    surfaces as `action_result: {"status": "failed", ...}` so the model can
    tell the user it didn't go through, rather than the request 500ing or
    the failure being silently swallowed.

    Skips the interrupt entirely when `propose_action_node` couldn't
    confidently parse an action, falling straight through to the "unclear"
    branch instead of pausing for a confirmation that has nothing concrete
    to confirm. On resume, LangGraph re-runs this node's entire function
    body from the top (see the module comment above) — everything before
    `interrupt()` here is just the cheap `state.get("proposed_action")`
    read above, so redoing it costs nothing.
    """
    proposed_action = state.get("proposed_action")
    if proposed_action is None:
        return {"action_result": {"status": "unclear"}}

    decision = interrupt(proposed_action)
    if decision == "reject":
        return {"action_result": {"status": "cancelled", "summary": proposed_action["summary"]}}
    if decision != "confirm":
        return {"action_result": {"status": "unclear"}}

    cookie = config["configurable"].get("cookie")
    tool = proposed_action["tool"]
    args = proposed_action["args"]
    try:
        if tool == "create_log":
            await mcp_tools.create_log(cookie, args)
        elif tool == "create_schedule":
            await mcp_tools.create_schedule(cookie, args)
        else:
            # Shouldn't happen — `propose_action_node` only ever sets
            # `tool` to one of these two — but degrades loudly instead of
            # silently defaulting to `create_schedule` for anything
            # unrecognized (caught in code review).
            logger.warning("execute_action_node got an unrecognized tool %r", tool)
            return {"action_result": {"status": "unclear"}}
    except httpx.HTTPStatusError as exc:
        logger.warning("action execution rejected by core-api", exc_info=True)
        return {
            "action_result": {
                "status": "failed",
                "detail": mcp_tools.extract_error_detail(exc),
            }
        }
    except Exception:
        logger.warning("action execution failed unexpectedly", exc_info=True)
        return {"action_result": {"status": "failed", "detail": "an unexpected error occurred"}}

    return {"action_result": {"status": "done", "summary": proposed_action["summary"]}}
