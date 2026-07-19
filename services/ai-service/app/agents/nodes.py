import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from app import citations as citations_module
from app import entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.agents.state import (
    GENERAL_PERSONA,
    MAINTENANCE_PERSONA,
    RESEARCH_PERSONA,
    VALID_AGENTS,
    VEHICLE_PERSONA,
    AgentState,
)
from app.config import settings

logger = logging.getLogger(__name__)

_SUPERVISOR_SYSTEM_PROMPT = (
    "You are a routing classifier for a household operations assistant. "
    "Classify the user's question into exactly one category: "
    "'maintenance' (general upkeep, service history, or schedules across "
    "any tracked home, vehicle, equipment, or project), 'vehicle' "
    "(specifically about a car, truck, or other vehicle), 'research' "
    "(about the content of an uploaded document, manual, receipt, or "
    "invoice), or 'general' (anything else, or if you're unsure). "
    "Respond with exactly one word: maintenance, vehicle, research, or general."
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
    """
    try:
        raw = await ollama.complete(
            [
                {"role": "system", "content": _SUPERVISOR_SYSTEM_PROMPT},
                {"role": "user", "content": state["query"]},
            ]
        )
        selected_agent = get_adapter().parse_choice(raw, VALID_AGENTS) or "general"
    except Exception:
        logger.warning("agent routing failed, defaulting to general", exc_info=True)
        selected_agent = "general"
    return {"selected_agent": selected_agent}


async def gather_baseline_context(
    query: str, cookie: str | None
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
    """
    entity_context_task = asyncio.create_task(
        entity_grounding.gather_entity_context(query, cookie)
    )
    chunks = await retrieval.retrieve_context(query)
    citation_list, entity_context = await asyncio.gather(
        citations_module.resolve_citations(cookie, chunks),
        entity_context_task,
    )
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
