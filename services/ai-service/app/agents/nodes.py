import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import get_args

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app import citations as citations_module
from app import core_api_client, entity_grounding, mcp_tools, ollama, retrieval
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
from app.providers import (
    ForecastResult,
    SearchResult,
    WeatherResult,
    get_search_provider,
    get_weather_provider,
)
from app.providers.weather import MAX_FORECAST_DAYS
from app.schemas.chat import Citation
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
    "invoice; needs current/live information from the internet — news, a "
    "person's employer, a company; or asks about the weather), "
    "'log_or_schedule' (the user is asking you to record, log, "
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
    "You are the household's Research Assistant. You have four tools "
    "available:\n"
    "- search_household_documents(query): searches the household's "
    "uploaded documents (manuals, receipts, invoices) for relevant "
    "excerpts.\n"
    "- search_web(query): searches the live internet — use this for "
    "current events, news about a person/company, or anything that needs "
    "up-to-date information the household's own records wouldn't have.\n"
    "- get_weather(location): CURRENT weather only, right now, for a named "
    "place. Omit location entirely if the user didn't name one — it will "
    "fall back to the household's default location if it has one set. "
    "NEVER invent or assume a place (e.g. don't default to \"San "
    "Francisco\" or anywhere else) just because the tool call needs a "
    "value — an omitted location is always correct when none was named.\n"
    "- get_weather_forecast(location, days): a day-by-day outlook (rain "
    f"amount and conditions per day, up to {MAX_FORECAST_DAYS} days ahead) "
    "for a named place. Use this — not get_weather — for anything about "
    "*future* or *upcoming* weather: whether it'll rain on a given day, "
    "finding a stretch of dry days, planning around the forecast. You'll "
    "get raw day-by-day data back and can reason over it yourself — you "
    "don't need the answer already computed for you. location and days "
    "are both optional (location falls back like get_weather — never "
    "invent one — and days defaults to "
    f"{MAX_FORECAST_DAYS}).\n\n"
    "Decide whether you need any tool to answer the user's question. "
    "Respond with ONLY a JSON object, no other text: "
    '{"tool": "search_household_documents"|"search_web", "query": "<search '
    'terms>"} to search, {"tool": "get_weather", "location": "<place>"} '
    '(location is optional) to check current weather, {"tool": '
    '"get_weather_forecast", "location": "<place>", "days": <number>} '
    "(location and days are both optional) to check the forecast, or "
    '{"tool": null} if you already have enough information or the '
    "question doesn't need any tool."
)

_GENERAL_GROUNDING_GATE_PROMPT = (
    "You help decide whether answering a household assistant's question "
    "needs to check the household's own records first — its uploaded "
    "documents (manuals, receipts, invoices) and tracked entities (people, "
    "vehicles, equipment, projects, schedules). Most questions benefit from "
    "checking, even loosely related ones, so default to yes unless you're "
    "genuinely confident the question has nothing to do with the "
    "household's own data — e.g. small talk, thanks, a question about the "
    "conversation itself (\"what were we just talking about\"), or a pure "
    "general-knowledge question with no household angle at all. When in "
    "doubt, answer yes. Respond with exactly one word: yes or no."
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

    Includes `state["history"]` (a handful of prior turns, see
    `routers/chat.py::_routing_history`) ahead of the current query —
    without it, a short follow-up ("what about Warner Robins?") carries no
    signal on its own and reliably falls through to "general" regardless
    of what the conversation was actually about (caught in code review,
    after live use surfaced exactly this: a correctly-routed weather
    forecast question's own follow-up lost the topic entirely).
    """
    try:
        raw = await ollama.complete(
            [
                {"role": "system", "content": _SUPERVISOR_SYSTEM_PROMPT},
                *state.get("history", []),
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
    household_id: str | None,
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
        chunks = await retrieval.retrieve_context(query, household_id)
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
    household_id = config["configurable"].get("household_id")
    entity_context, chunks, citation_list = await gather_baseline_context(
        state["query"], cookie, household_id
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
    """Fallback when the supervisor can't confidently classify. Unlike
    `maintenance_node`/`vehicle_node` (always blanket-gather — see
    `_gather_household_and_documents`'s docstring for why that's
    deliberate for them: skipping it there once caused a real regression),
    `general_node` catches everything the other categories didn't want,
    including pure conversational/meta questions with no information need
    at all ("what were we talking about?", "thanks!"). Blanket-searching
    for those wastes a Chroma round trip and, worse, a short/generic
    enough query can still land inside `settings.rag_max_distance` against
    something in the household's documents by embedding-noise alone,
    surfacing a citation with nothing to do with the answer (caught in
    code review, after live use surfaced exactly this).

    A single gate call decides whether to bother at all — deliberately
    biased toward "yes" (search), never "no", whenever there's any real
    doubt: skipping when grounding was actually needed is the worse
    failure (the regression `_gather_household_and_documents` already
    guards against), while searching when it wasn't just costs one extra
    round trip. A second "does the model agree with its own first
    answer" pass was considered and deliberately not built — the same
    model re-examining its own just-made judgment call, with no new
    information, mostly rubber-stamps it rather than catching a mistake;
    it would add a full extra round trip to every general-routed turn for
    little real protection. The actual protection is the yes-bias itself:
    any parse failure or exception here also defaults to "yes", same
    reasoning — this gate is additive (an optimization), never
    load-bearing, same contract as every other node.
    """
    query = state["query"]
    try:
        raw = await ollama.complete(
            [
                {"role": "system", "content": _GENERAL_GROUNDING_GATE_PROMPT},
                {"role": "user", "content": query},
            ]
        )
        # Only a conclusive "no" skips grounding — an unparseable reply
        # (`parse_choice` returns `None`) falls through to the same "yes"
        # default as an outright failure below.
        needs_grounding = get_adapter().parse_choice(raw, ["yes", "no"]) != "no"
    except Exception:
        logger.warning("general grounding gate failed, defaulting to searching", exc_info=True)
        needs_grounding = True

    if not needs_grounding:
        return {
            "entity_context": [],
            "chunks": [],
            "citation_list": [],
            "persona": GENERAL_PERSONA,
            "tool_calls_made": ["grounding_gate"],
        }

    return await _gather_household_and_documents(state, config, GENERAL_PERSONA)


def _most_recent_log_date(entity_context: list[entity_grounding.EntityContext]) -> date | None:
    """The `since` bound for `search_web` (M10) — derived from the most
    recent log across every matched entity, per the roadmap's explicit
    "prevent pulling up old stories" requirement: a web search about a
    person/company should never surface news older than what's already
    known about them. `occurred_at` is an ISO date string on the wire
    (`aria_shared.schemas.LogEntry.occurred_at`, serialized over JSON) —
    parsed defensively since it's read from an untyped dict, not the
    Pydantic model itself.
    """
    dates: list[date] = []
    for entity in entity_context:
        for log in entity.logs:
            occurred_at = log.get("occurred_at")
            if not occurred_at:
                continue
            try:
                dates.append(date.fromisoformat(occurred_at))
            except ValueError:
                continue
    return max(dates) if dates else None


async def _resolve_weather_location(cookie: str | None) -> str | None:
    """Falls back to the household's default `city` (M10 signup field)
    when the model's `get_weather` decision didn't name a place —
    `None` means genuinely no location is available (no cookie, no city
    set, or core-api unreachable), in which case the caller skips the
    lookup entirely rather than guessing.
    """
    if cookie is None:
        return None
    household = await core_api_client.get_household(cookie)
    return household.get("city") if household else None


def _validated_location(decision_location: str | None, query: str) -> str | None:
    """The tool-choice model has a well-documented bias toward inventing a
    place (observed live: defaulting to "San Francisco") instead of
    actually omitting `location` when the user's query never named one,
    despite the prompt saying not to — trust the decision's location only
    when its core place name is actually present in the query text;
    otherwise treat it the same as an omitted location, so the caller's
    `or _resolve_weather_location(cookie)` fallback engages instead of a
    hallucinated guess (caught in code review, after live use surfaced
    exactly this). Checked on just the portion before a comma (e.g.
    "Lizella" out of "Lizella, GA") so a model that reasonably qualifies a
    bare place name the user *did* say (adding a state/country) isn't
    rejected alongside one that invented the whole place.
    """
    if not decision_location:
        return None
    core_name = decision_location.split(",", 1)[0].strip().lower()
    if core_name and core_name in query.lower():
        return decision_location
    return None


def _citation_from_search_result(result: SearchResult) -> Citation:
    return Citation(
        source_type="web",
        url=result.url,
        title=result.title,
        snippet=result.snippet,
    )


def _citation_from_weather(location: str, result: WeatherResult) -> Citation:
    return Citation(
        source_type="web",
        url="https://open-meteo.com/",
        title=f"Weather for {result.location_label}",
        snippet=(
            f"{result.temperature_c}°C, {result.condition}, "
            f"wind {result.wind_kph} kph"
        ),
    )


def _citation_from_forecast(result: ForecastResult) -> Citation:
    """Raw day-by-day data, not a pre-computed answer — finding whatever
    the user actually asked for (a dry stretch, a specific day's chance of
    rain) is left to the model's own reasoning over this, the same
    division of labor `get_weather_forecast`'s tool description promises.
    """
    lines = [
        f"{day.day.isoformat()}: {day.condition}, {day.precipitation_mm}mm precipitation"
        for day in result.days
    ]
    return Citation(
        source_type="web",
        url="https://open-meteo.com/",
        title=f"{len(result.days)}-day forecast for {result.location_label}",
        snippet="\n".join(lines),
    )


def _dedup_web_citations(citations: list[Citation]) -> list[Citation]:
    """Two reformulated `search_web` calls in the same turn can plausibly
    return an overlapping URL — collapse to the first occurrence, same
    reasoning `retrieval.dedup_chunks` already applies to document search
    (caught in code review). Keyed on `(url, title)`, not `url` alone —
    every `get_weather`/`get_weather_forecast` citation shares the same
    static `https://open-meteo.com/` URL regardless of location, so `url`
    alone would wrongly collapse two different locations' weather/forecast
    citations called in the same turn into one.
    """
    seen: set[tuple[str | None, str | None]] = set()
    deduped: list[Citation] = []
    for citation in citations:
        key = (citation.url, citation.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


@dataclass
class ToolContext:
    """Shared, mutable state threaded through every `research_node` tool
    handler for one turn — the one place a handler reads the turn's
    query/cookie/household_id or lazily resolves entity context, so
    adding a new tool never means duplicating any of that setup, and
    `research_node`'s own loop never needs to know a given tool exists.
    """

    query: str
    cookie: str | None
    household_id: str | None
    entity_context_task: "asyncio.Task[list[entity_grounding.EntityContext]]"
    entity_context: list[entity_grounding.EntityContext] | None = None
    web_searches_made: int = 0

    async def get_entity_context(self) -> list[entity_grounding.EntityContext]:
        """Awaits `entity_context_task` at most once per turn, caching the
        result — every handler that needs entity context calls this
        instead of each hand-rolling its own "already resolved?" check.
        """
        if self.entity_context is None:
            self.entity_context = await self.entity_context_task
        return self.entity_context


@dataclass
class ToolResult:
    """What a tool handler hands back to `research_node`'s loop. Never
    raises itself — a handler that fails should just let the exception
    propagate; the loop's own try/except degrades the same way regardless
    of which tool failed. A `None` `tool_name` means the tool wasn't
    actually dispatched this turn (e.g. `get_weather` with no location
    available anywhere) — the loop only records a non-`None` name into
    `tool_calls_made`.
    """

    tool_name: str | None
    scratchpad_entry: str | None = None
    chunks: list[retrieval.RetrievedChunk] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)


async def _search_household_documents_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    search_query = decision.get("query") or ctx.query
    new_chunks = await retrieval.retrieve_context(search_query, ctx.household_id)
    return ToolResult(
        tool_name="search_household_documents",
        chunks=new_chunks,
        scratchpad_entry=(
            f'Already searched the household\'s documents for "{search_query}" '
            f"and found {len(new_chunks)} excerpt(s)."
        ),
    )


async def _search_web_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    entity_context = await ctx.get_entity_context()
    search_query = decision.get("query") or ctx.query
    # Only the first web search of the turn gets the matched entities'
    # recency cutoff — a later, reformulated search_web call (this loop
    # explicitly allows reformulation) may be about an unrelated subject,
    # and reapplying the first search's cutoff to it could wrongly filter
    # out relevant recent results (caught in code review).
    since = _most_recent_log_date(entity_context) if ctx.web_searches_made == 0 else None
    results = await get_search_provider().search(search_query, since=since)
    ctx.web_searches_made += 1
    return ToolResult(
        tool_name="search_web",
        citations=[_citation_from_search_result(r) for r in results],
        scratchpad_entry=(
            f'Already searched the web for "{search_query}" and found '
            f"{len(results)} result(s)."
        ),
    )


async def _get_weather_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    location = _validated_location(
        decision.get("location"), ctx.query
    ) or await _resolve_weather_location(ctx.cookie)
    if not location:
        return ToolResult(
            tool_name=None,
            scratchpad_entry=(
                "Tried to check the weather, but no location was given and "
                "the household has no default location set."
            ),
        )
    result = await get_weather_provider().get_weather(location)
    if result is None:
        return ToolResult(
            tool_name="get_weather",
            scratchpad_entry=f'Tried to check the weather for "{location}" but got no result.',
        )
    return ToolResult(
        tool_name="get_weather",
        citations=[_citation_from_weather(location, result)],
        scratchpad_entry=f'Already checked the weather for "{location}".',
    )


async def _get_weather_forecast_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    location = _validated_location(
        decision.get("location"), ctx.query
    ) or await _resolve_weather_location(ctx.cookie)
    if not location:
        return ToolResult(
            tool_name=None,
            scratchpad_entry=(
                "Tried to check the forecast, but no location was given and "
                "the household has no default location set."
            ),
        )
    try:
        days = int(decision.get("days") or MAX_FORECAST_DAYS)
    except (TypeError, ValueError):
        days = MAX_FORECAST_DAYS
    forecast = await get_weather_provider().get_forecast(location, days=days)
    if forecast is None:
        return ToolResult(
            tool_name="get_weather_forecast",
            scratchpad_entry=f'Tried to check the forecast for "{location}" but got no result.',
        )
    return ToolResult(
        tool_name="get_weather_forecast",
        citations=[_citation_from_forecast(forecast)],
        scratchpad_entry=f'Already checked the {len(forecast.days)}-day forecast for "{location}".',
    )


# Adding a tool to the Research Assistant's loop means: write a handler
# above with this exact `(decision, ctx) -> ToolResult` shape, then add one
# entry here — `research_node`'s own loop never changes. A plain dict
# lookup, not a decorator-based registration side effect, matching the
# provider registries `app/providers/__init__.py` already uses (caught in
# code review: the old inline if/elif chain meant every new tool touched
# `research_node` itself, and had grown from 1 to 4 branches already).
_RESEARCH_TOOL_HANDLERS: dict[str, Callable[[dict, ToolContext], Awaitable[ToolResult]]] = {
    "search_household_documents": _search_household_documents_tool,
    "search_web": _search_web_tool,
    "get_weather": _get_weather_tool,
    "get_weather_forecast": _get_weather_forecast_tool,
}


async def research_node(state: AgentState, config: RunnableConfig) -> dict:
    """Bounded tool-choice loop, capped at `settings.agent_max_tool_calls`
    iterations — the one specialist where genuine iterative tool use (not
    just a fixed context-gathering step) earns its keep: deciding whether
    to search (documents or the web), check current weather or a multi-day
    forecast, optionally reformulating the query, and whether to call
    another tool after seeing the first result. Any parse failure or
    exception just ends the loop early rather than raising — synthesis
    proceeds with whatever was gathered so far, same degrade-don't-fail
    contract as every other node. Dispatch itself is a lookup into
    `_RESEARCH_TOOL_HANDLERS` — this function only owns the loop, the
    LLM call, and turning each handler's `ToolResult` into scratchpad/
    `tool_calls_made` bookkeeping; it has no per-tool knowledge at all.

    Also gathers baseline entity context, same as the other specialists
    (an earlier version didn't — code review caught that as a real
    regression: without it, `build_system_prompt()`'s citation-to-entity
    cross-referencing, e.g. "this document is linked to Dad", could never
    fire for a research-routed answer even when the cited document really
    is linked to a matched household entity). Kicked off as a concurrent
    task rather than awaited up front so it overlaps with the tool-choice
    loop's own LLM calls instead of adding sequential latency — *unless*
    `search_web` is used, which needs the matched entities' most recent
    log date (`_most_recent_log_date`) as its `since` cutoff and so must
    await it eagerly, at most once per turn (`ToolContext.get_entity_
    context()` is the "not yet awaited" check — every handler shares the
    one `ctx` instance, so it's a single cache regardless of which
    handler resolves it first).
    """
    query = state["query"]
    cookie = config["configurable"].get("cookie")
    household_id = config["configurable"].get("household_id")
    ctx = ToolContext(
        query=query,
        cookie=cookie,
        household_id=household_id,
        entity_context_task=asyncio.create_task(
            entity_grounding.gather_entity_context(query, cookie)
        ),
    )

    chunks: list[retrieval.RetrievedChunk] = []
    web_citations: list[Citation] = []
    tool_calls_made: list[str] = ["gather_household_context"]
    scratchpad: list[str] = []

    history = state.get("history", [])
    for _ in range(settings.agent_max_tool_calls):
        # `history` ahead of the current query, same reasoning as
        # `supervisor_node` — a follow-up like "what about Warner Robins?"
        # needs the previous turn to know it's still about the forecast at
        # all, let alone which tool to reach for (caught in code review).
        messages = [
            {"role": "system", "content": _RESEARCH_TOOL_SYSTEM_PROMPT},
            *history,
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

        handler = _RESEARCH_TOOL_HANDLERS.get(tool)
        if handler is None:
            # An unrecognized tool name — shouldn't happen given the prompt,
            # but degrades by ending the loop rather than looping forever
            # on a decision nothing here knows how to dispatch.
            logger.warning("research tool-choice named an unrecognized tool %r", tool)
            break

        try:
            result = await handler(decision, ctx)
        except Exception:
            # A misconfigured provider (`get_search_provider`/
            # `get_weather_provider` raise `ValueError` on an unrecognized
            # `AI_SERVICE_SEARCH_PROVIDER`/`AI_SERVICE_WEATHER_PROVIDER`)
            # or any other tool-dispatch failure must degrade like every
            # other failure in this loop, not escape `research_node`
            # entirely and discard whatever chunks/citations were already
            # gathered this turn (caught in code review) — applies
            # uniformly to every handler, not just the ones that existed
            # when this was first written.
            logger.warning("research tool dispatch failed for tool %r, stopping loop", tool, exc_info=True)
            break

        if result.tool_name is not None:
            tool_calls_made.append(result.tool_name)
        if result.scratchpad_entry:
            scratchpad.append(result.scratchpad_entry)
        chunks.extend(result.chunks)
        web_citations.extend(result.citations)

    # Two reformulated searches can legitimately return the same top chunk
    # (e.g. "water heater manual" vs. "water heater instructions" both
    # nearest-matching the same page) — dedup before it's rendered twice
    # into the prompt (caught in code review).
    chunks = retrieval.dedup_chunks(chunks)
    # Same reasoning applies to web citations from possibly-multiple
    # search_web calls in one turn (caught in code review).
    web_citations = _dedup_web_citations(web_citations)

    if ctx.entity_context is not None:
        doc_citations = await citations_module.resolve_citations(cookie, chunks)
        entity_context = ctx.entity_context
    else:
        doc_citations, entity_context = await asyncio.gather(
            citations_module.resolve_citations(cookie, chunks),
            ctx.entity_context_task,
        )
    return {
        "chunks": chunks,
        "entity_context": entity_context,
        "citation_list": doc_citations + web_citations,
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
    household_id = config["configurable"].get("household_id")
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
        gather_baseline_context(query, cookie, household_id, entities, baseline_matched)
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


# Both write-path tools share this exact `(cookie, args) -> dict` shape
# already (`mcp_tools.create_log`/`create_schedule`), so — unlike
# `research_node`'s handlers, which each need their own bespoke
# ToolContext/ToolResult bookkeeping — a small dict of wrappers is enough
# of a registry here; `execute_action_node` below just looks the tool up
# and awaits it the same way for any tool in this dict, instead of an
# if/elif naming each one. Each entry looks its function up on `mcp_tools`
# at call time (not `mcp_tools.create_log` captured directly into the
# dict at import time) so tests that monkeypatch `mcp_tools.create_log`/
# `create_schedule` still take effect — a dict built from the bound
# functions themselves would freeze in the pre-patch versions.
_ACTION_EXECUTORS: dict[str, Callable[[str, dict], Awaitable[dict]]] = {
    "create_log": lambda cookie, args: mcp_tools.create_log(cookie, args),
    "create_schedule": lambda cookie, args: mcp_tools.create_schedule(cookie, args),
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
    executor = _ACTION_EXECUTORS.get(tool)
    if executor is None:
        # Shouldn't happen — `propose_action_node` only ever sets `tool`
        # to one of `_ACTION_EXECUTORS`'s keys — but degrades loudly
        # instead of silently defaulting to one of them for anything
        # unrecognized (caught in code review).
        logger.warning("execute_action_node got an unrecognized tool %r", tool)
        return {"action_result": {"status": "unclear"}}
    try:
        await executor(cookie, args)
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
