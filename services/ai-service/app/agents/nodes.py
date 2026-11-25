import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import get_args

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app import citations as citations_module
from app import core_api_client, entity_grounding, mcp_tools, ollama, retrieval
from app.agents.state import (
    ACTION_PERSONA,
    GENERAL_PERSONA,
    MAINTENANCE_PERSONA,
    RESEARCH_PERSONA,
    VALID_AGENTS,
    VEHICLE_PERSONA,
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
from aria_shared.models.entities import ENTITY_DOMAINS
from aria_shared.models.logs import LogType
from aria_shared.schemas import LogCreate, ScheduleCreate

logger = logging.getLogger(__name__)


def _stream_status(status: str) -> None:
    """Pushes a line to the live SSE preview as a `status` frame. A no-op
    outside a graph run; nothing depends on a status line arriving.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"status": status})


_SUPERVISOR_SYSTEM_PROMPT = (
    "You are a routing classifier for a household operations assistant. "
    "Pick exactly one category for the user's latest message.\n"
    "- 'action': the user wants something recorded or scheduled: logging "
    "something that already happened ('log that...', 'I just...', 'record "
    "that...', 'I changed/replaced/fixed...') or creating a reminder or "
    "recurring schedule ('remind me to...', 'schedule...').\n"
    "- 'vehicle': a question about a specific car, truck, or other vehicle.\n"
    "- 'maintenance': a question about upkeep, service history, or schedules "
    "across the household's home, equipment, or projects.\n"
    "- 'research': a question about the content of an uploaded document, "
    "manual, receipt, or invoice; anything needing live information from "
    "the internet (news, a person's employer, a company); or the weather.\n"
    "- 'general': anything else, or if unsure.\n"
    "Rules: if the user is asking you to record or schedule something, "
    "choose 'action' even when it involves a vehicle or maintenance. Choose "
    "'vehicle' over 'maintenance' for questions about a vehicle. Give a "
    "one-sentence reason before the category."
)

_SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 200},
        "agent": {"type": "string", "enum": list(VALID_AGENTS)},
    },
    "required": ["reason", "agent"],
}

LOG_TYPE_DESCRIPTIONS = {
    "service": "routine upkeep or replacement of a wear item (oil change, filter change, tire rotation, cleaning)",
    "repair": "fixing something broken or damaged",
    "inspection": "checking condition without doing work (annual inspection, looking over the roof)",
    "expense": "a purchase or cost with no work involved",
    "note": "a general observation",
    "milestone": "a notable event (bought, moved in, project phase finished)",
    "conversation": "an in-person conversation with a person",
    "call": "a phone or video call with a person",
    "meeting": "a planned meeting with a person",
    "gift": "a gift given to or received from a person",
}
assert set(LOG_TYPE_DESCRIPTIONS) == set(get_args(LogType)), (
    "LOG_TYPE_DESCRIPTIONS must describe exactly the LogType values"
)
INTERVAL_TYPE_DESCRIPTIONS = {
    "time": "repeats on a fixed interval of days, weeks, months or years (every 90 days, every 6 months, yearly)",
    "usage": "repeats after an amount of use such as miles or hours (every 5000 miles)",
    "once": "a single reminder on one date",
    "monthly": "repeats on the same day number (the 15th) or weekday (the first Monday) of every month",
}
assert set(INTERVAL_TYPE_DESCRIPTIONS) == set(
    get_args(ScheduleCreate.model_fields["interval_type"].annotation)
), "INTERVAL_TYPE_DESCRIPTIONS must describe exactly ScheduleCreate's interval types"

ACTION_TOOLS = ("create_log", "create_schedule")
NO_ACTION = "none"

ACTION_TARGET_SYSTEM_PROMPT = (
    "You are ARIA, a household operations assistant. Decide what the user's "
    "message asks you to do. Give a one-sentence reason first, then set tool "
    "to one of: 'create_log': the user tells you about something they "
    "already did or that already happened, to be recorded; "
    "'create_schedule': the user asks to be reminded of something, or to "
    "schedule something upcoming or recurring; 'none': anything else, "
    "including any question (should I..., how much..., when...). Set "
    "entity_id to the household entity from the list the message is about, "
    "or null when tool is 'none' or you can't tell which one."
)

LOG_FIELDS_SYSTEM_PROMPT = (
    "You are filling in a log entry the user asked ARIA to record about "
    "{entity}. Set type to the one that fits best: {log_types}. Set "
    "occurred_at to the date it happened as YYYY-MM-DD, using the calendar "
    "below; if the user gave no date, use today. Set title to a short label "
    "such as 'Oil change'. Set description to any extra detail the user "
    "gave, or null. Set cost to the amount spent as a plain number, or null "
    "if none was mentioned. Set metrics to readings the user gave, such as "
    '{{"mileage": "61500"}}, or {{}} if none.'
)

SCHEDULE_KIND_SYSTEM_PROMPT = (
    "You are setting up a schedule the user asked ARIA for, about {entity}. "
    "Give a one-sentence reason first. Set interval_type to the one that "
    "fits best: {interval_types}. Set title to a short label for what is "
    "due, such as 'Replace furnace filter'."
)

SCHEDULE_DETAIL_SYSTEM_PROMPTS = {
    "time": (
        "The user wants '{title}' for {entity} to repeat on a fixed interval. "
        "Set every and unit to how often it repeats: every 3 months is every 3, "
        "unit months. Set starting_at to the date it was last done as "
        "YYYY-MM-DD if the user said, using the calendar below, or null."
    ),
    "usage": (
        "The user wants '{title}' for {entity} to repeat after an amount of "
        "use. Set usage_metric to what is measured, as one lowercase word "
        "such as mileage or hours. Set interval_usage_amount to how much use "
        "comes between each time, as a plain number. Set starting_usage_value "
        "to the reading when it was last done if the user said, or null."
    ),
    "once": (
        "The user wants a one-time reminder, '{title}', for {entity}. Set "
        "month and day to the date it is due, using the calendar below for "
        "days of the week. Set year only if the user said a year, else null."
    ),
    "monthly": (
        "The user wants '{title}' for {entity} to repeat every month. Set "
        "when to the user's own words for which day of the month it falls "
        "on, such as 'first Saturday', 'last Friday' or '15th'. Set "
        "last_done to the user's own words for when it was last done, such "
        "as 'November 1' or 'last Saturday', or null if they didn't say."
    ),
}

LAST_DONE_SYSTEM_PROMPT = (
    "The user said '{title}' for {entity} was last done {last_done}. Set "
    "month and day to that date, using the calendar below for days of the "
    "week. Set year only if the user said a year, else null."
)
assert set(SCHEDULE_DETAIL_SYSTEM_PROMPTS) == set(INTERVAL_TYPE_DESCRIPTIONS)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_WORDS = {
    **{name: index for index, name in enumerate(_WEEKDAYS)},
    **{name[:3]: index for index, name in enumerate(_WEEKDAYS)},
    "tues": 1,
    "weds": 2,
    "thur": 3,
    "thurs": 3,
}
_WEEK_INDEXES = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": -1}
_WEEK_WORDS = {**_WEEK_INDEXES, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_WEEKDAY_OF_MONTH = re.compile(
    rf"\b({'|'.join(_WEEK_WORDS)})\s+({'|'.join(sorted(_WEEKDAY_WORDS, key=len, reverse=True))})\b",
    re.IGNORECASE,
)
_DAY_OF_MONTH = re.compile(r"\b([1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)?\b")
_ORDINAL_UNITS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth")
_ORDINAL_TEENS = (
    "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
    "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth",
)
_ORDINAL_DAYS = {
    **{word: day for day, word in enumerate(_ORDINAL_UNITS, start=1)},
    **{word: day for day, word in enumerate(_ORDINAL_TEENS, start=10)},
    **{f"twenty-{word}": 20 + day for day, word in enumerate(_ORDINAL_UNITS, start=1)},
    "thirtieth": 30,
    "thirty-first": 31,
}
_ORDINAL_DAY_OF_MONTH = re.compile(
    rf"\b({'|'.join(sorted(_ORDINAL_DAYS, key=len, reverse=True))})\b", re.IGNORECASE
)
_DAY_WORDS = re.compile(
    rf"\d|\b({'|'.join(sorted({*_ORDINAL_DAYS, *_WEEK_WORDS, *_WEEKDAY_WORDS}, key=len, reverse=True))})\b",
    re.IGNORECASE,
)


class UnclearMonthlyDay(ValueError):
    """The monthly phrase names no day, several, or one a schedule can't
    hold. A deliberate refusal: the turn proposes no card."""


_UNREPRESENTABLE_DAYS = re.compile(r"\b(business|working|weekdays?|weekends?|to[- ]last)\b", re.IGNORECASE)
_UNIT_DAYS = {"days": 1, "weeks": 7, "months": 30, "years": 365}
_DATED_INTERVAL_TYPES = ("time", "once")

SCHEDULE_DETAIL_SCHEMAS = {
    "time": {
        "type": "object",
        "properties": {
            "every": {"type": "integer", "minimum": 1},
            "unit": {"type": "string", "enum": list(_UNIT_DAYS)},
            "starting_at": {"type": ["string", "null"]},
        },
        "required": ["every", "unit", "starting_at"],
    },
    "usage": {
        "type": "object",
        "properties": {
            "usage_metric": {"type": "string", "maxLength": 40},
            "interval_usage_amount": {"type": "number"},
            "starting_usage_value": {"type": ["number", "null"]},
        },
        "required": ["usage_metric", "interval_usage_amount", "starting_usage_value"],
    },
    "once": {
        "type": "object",
        "properties": {
            "month": {"type": "integer", "minimum": 1, "maximum": 12},
            "day": {"type": "integer", "minimum": 1, "maximum": 31},
            "year": {"type": ["integer", "null"]},
        },
        "required": ["month", "day", "year"],
    },
    "monthly": {
        "type": "object",
        "properties": {
            "when": {"type": "string", "maxLength": 40},
            "last_done": {"type": ["string", "null"], "maxLength": 40},
        },
        "required": ["when", "last_done"],
    },
}
LAST_DONE_SCHEMA = SCHEDULE_DETAIL_SCHEMAS["once"]


def action_target_schema(entity_ids: list[str]) -> dict:
    """Built per request so `entity_id` can only be one of this turn's
    matched entities."""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "maxLength": 200},
            "tool": {"type": "string", "enum": [*ACTION_TOOLS, NO_ACTION]},
            "entity_id": {"type": ["string", "null"], "enum": [*entity_ids, None]},
        },
        "required": ["reason", "tool", "entity_id"],
    }


def log_fields_schema(log_types: tuple[str, ...]) -> dict:
    """`type` is narrowed to the log types valid for the entity's domain."""
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(log_types)},
            "occurred_at": {"type": "string"},
            "title": {"type": "string", "maxLength": 120},
            "description": {"type": ["string", "null"]},
            "cost": {"type": ["number", "null"]},
            "metrics": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["type", "occurred_at", "title", "description", "cost", "metrics"],
    }


SCHEDULE_KIND_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 200},
        "interval_type": {"type": "string", "enum": list(INTERVAL_TYPE_DESCRIPTIONS)},
        "title": {"type": "string", "maxLength": 120},
    },
    "required": ["reason", "interval_type", "title"],
}

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
    "Decide whether you need any tool to answer the user's question. Set "
    "tool to the one you need, with query for a search or location/days "
    "for a weather lookup as appropriate, or set tool to null if you "
    "already have enough information or the question doesn't need any "
    "tool."
)

_GENERAL_GROUNDING_GATE_PROMPT = (
    "You help decide whether answering a household assistant's question "
    "needs to check the household's own records first — its uploaded "
    "documents (manuals, receipts, invoices) and tracked entities (people, "
    "vehicles, equipment, projects, schedules). Most questions benefit from "
    "checking, even loosely related ones, so default to needing a check "
    "unless you're genuinely confident the question has nothing to do with "
    "the household's own data — e.g. small talk, thanks, a question about "
    "the conversation itself (\"what were we just talking about\"), or a "
    "pure general-knowledge question with no household angle at all. When "
    "in doubt, say a check is needed."
)

_GROUNDING_GATE_SCHEMA = {
    "type": "object",
    "properties": {"needs_grounding": {"type": "boolean"}},
    "required": ["needs_grounding"],
}


async def supervisor_node(state: AgentState, config: RunnableConfig) -> dict:
    """Picks one of `VALID_AGENTS` with one constrained call, seeing recent
    history so short follow-ups route by topic. Any failure routes to
    `general`.
    """
    def _extract_agent(decision: dict) -> str:
        agent = decision.get("agent")
        return agent if agent in VALID_AGENTS else "general"

    selected_agent = await ollama.complete_structured_or_default(
        [
            {"role": "system", "content": _SUPERVISOR_SYSTEM_PROMPT},
            *state.get("history", []),
            {"role": "user", "content": state["query"]},
        ],
        _SUPERVISOR_SCHEMA,
        extract=_extract_agent,
        default="general",
        warning="agent routing failed, defaulting to general",
    )
    return {"selected_agent": selected_agent}


async def gather_baseline_context(
    query: str,
    cookie: str | None,
    household_id: str | None,
    entities: list[dict] | None = None,
    matched: list[dict] | None = None,
) -> tuple[list[entity_grounding.EntityContext], list[retrieval.RetrievedChunk], list]:
    """Entity context, document chunks and their citations: every
    specialist's baseline and the fallback when the graph is down. Chunk
    retrieval runs first; citations and entity context then run
    concurrently. Pass `entities`/`matched` to skip refetching them.
    """
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
    """Baseline gather for maintenance, vehicle and general: always both
    entity context and documents, differing only in persona.
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


async def needs_grounding(query: str) -> bool:
    """A yes-biased gate call: False only when the question has nothing to
    do with the household (small talk, thanks). Any failure returns True.
    """
    return await ollama.complete_structured_or_default(
        [
            {"role": "system", "content": _GENERAL_GROUNDING_GATE_PROMPT},
            {"role": "user", "content": query},
        ],
        _GROUNDING_GATE_SCHEMA,
        extract=lambda decision: decision.get("needs_grounding", True),
        default=True,
        warning="general grounding gate failed, defaulting to searching",
    )


async def general_node(state: AgentState, config: RunnableConfig) -> dict:
    """Grounds the answer unless `needs_grounding` says the question has
    nothing to do with the household. Skipping avoids citations matched by
    embedding noise.
    """
    query = state["query"]
    if not await needs_grounding(query):
        return {
            "entity_context": [],
            "chunks": [],
            "citation_list": [],
            "persona": GENERAL_PERSONA,
            "tool_calls_made": ["grounding_gate"],
        }

    _stream_status("Checking household records…")
    return await _gather_household_and_documents(state, config, GENERAL_PERSONA)


def _most_recent_log_date(entity_context: list[entity_grounding.EntityContext]) -> date | None:
    """The `since` bound for `search_web`: the latest log date across the
    matched entities, so web results are never older than what's known.
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
    """The household's default city, or None when there's no location to
    use, in which case the caller skips the lookup.
    """
    if cookie is None:
        return None
    household = await core_api_client.get_household(cookie)
    return household.get("city") if household else None


def _validated_location(decision_location: str | None, query: str) -> str | None:
    """The model's location only if its place name (the part before any
    comma) appears in the query; otherwise None, since the model tends
    to invent a place when none was named.
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
    """Raw day-by-day forecast data; the answer model reasons over it."""
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
    """First occurrence of each `(url, title)`. Not keyed on url alone:
    every weather citation shares the same Open-Meteo url.
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


RESEARCH_SCRATCHPAD = {
    "documents": 'Already searched the household\'s documents for "{query}" and found {count} excerpt(s).',
    "web": 'Already searched the web for "{query}" and found {count} result(s).',
    "weather_no_location": (
        "Tried to check the weather, but no location was given and the "
        "household has no default location set."
    ),
    "weather_no_result": 'Tried to check the weather for "{location}" but got no result.',
    "weather": 'Already checked the weather for "{location}".',
    "forecast_no_location": (
        "Tried to check the forecast, but no location was given and the "
        "household has no default location set."
    ),
    "forecast_no_result": 'Tried to check the forecast for "{location}" but got no result.',
    "forecast": 'Already checked the {days}-day forecast for "{location}".',
}


@dataclass
class ToolContext:
    """Per-turn state shared by every research tool handler: the query,
    auth, and entity context resolved at most once.
    """

    query: str
    cookie: str | None
    household_id: str | None
    entity_context_task: "asyncio.Task[list[entity_grounding.EntityContext]]"
    entity_context: list[entity_grounding.EntityContext] | None = None
    web_searches_made: int = 0

    async def get_entity_context(self) -> list[entity_grounding.EntityContext]:
        """Awaits the entity-context task once and caches the result."""
        if self.entity_context is None:
            self.entity_context = await self.entity_context_task
        return self.entity_context


@dataclass
class ToolResult:
    """What a research tool handler returns. A None `tool_name` means the
    tool wasn't dispatched (e.g. weather with no location anywhere).
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
        scratchpad_entry=RESEARCH_SCRATCHPAD["documents"].format(query=search_query, count=len(new_chunks)),
    )


async def _search_web_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    entity_context = await ctx.get_entity_context()
    search_query = decision.get("query") or ctx.query
    since = _most_recent_log_date(entity_context) if ctx.web_searches_made == 0 else None
    results = await get_search_provider().search(search_query, since=since)
    ctx.web_searches_made += 1
    return ToolResult(
        tool_name="search_web",
        citations=[_citation_from_search_result(r) for r in results],
        scratchpad_entry=RESEARCH_SCRATCHPAD["web"].format(query=search_query, count=len(results)),
    )


async def _get_weather_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    location = _validated_location(
        decision.get("location"), ctx.query
    ) or await _resolve_weather_location(ctx.cookie)
    if not location:
        return ToolResult(
            tool_name=None,
            scratchpad_entry=RESEARCH_SCRATCHPAD["weather_no_location"],
        )
    result = await get_weather_provider().get_weather(location)
    if result is None:
        return ToolResult(
            tool_name="get_weather",
            scratchpad_entry=RESEARCH_SCRATCHPAD["weather_no_result"].format(location=location),
        )
    return ToolResult(
        tool_name="get_weather",
        citations=[_citation_from_weather(location, result)],
        scratchpad_entry=RESEARCH_SCRATCHPAD["weather"].format(location=location),
    )


async def _get_weather_forecast_tool(decision: dict, ctx: ToolContext) -> ToolResult:
    location = _validated_location(
        decision.get("location"), ctx.query
    ) or await _resolve_weather_location(ctx.cookie)
    if not location:
        return ToolResult(
            tool_name=None,
            scratchpad_entry=RESEARCH_SCRATCHPAD["forecast_no_location"],
        )
    raw_days = decision.get("days")
    if raw_days is None:
        days = MAX_FORECAST_DAYS
    else:
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            days = MAX_FORECAST_DAYS
    forecast = await get_weather_provider().get_forecast(location, days=days)
    if forecast is None:
        return ToolResult(
            tool_name="get_weather_forecast",
            scratchpad_entry=RESEARCH_SCRATCHPAD["forecast_no_result"].format(location=location),
        )
    return ToolResult(
        tool_name="get_weather_forecast",
        citations=[_citation_from_forecast(forecast)],
        scratchpad_entry=RESEARCH_SCRATCHPAD["forecast"].format(days=len(forecast.days), location=location),
    )


_RESEARCH_TOOL_HANDLERS: dict[str, Callable[[dict, ToolContext], Awaitable[ToolResult]]] = {
    "search_household_documents": _search_household_documents_tool,
    "search_web": _search_web_tool,
    "get_weather": _get_weather_tool,
    "get_weather_forecast": _get_weather_forecast_tool,
}

_RESEARCH_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": ["string", "null"], "enum": [*_RESEARCH_TOOL_HANDLERS, None]},
        "query": {"type": "string"},
        "location": {"type": "string"},
        "days": {"type": "integer"},
    },
    "required": ["tool"],
}


async def decide_research_tool(query: str, history: list[dict], scratchpad: list[str]) -> dict:
    """One tool-choice round: `{tool, query?, location?, days?}`, where a null
    `tool` means stop. `scratchpad` lists what earlier rounds already did.
    """
    messages = [
        {"role": "system", "content": _RESEARCH_TOOL_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": query},
    ]
    if scratchpad:
        messages.append({"role": "assistant", "content": "\n".join(scratchpad)})
    return await ollama.complete_structured(messages, _RESEARCH_TOOL_SCHEMA)


async def research_node(state: AgentState, config: RunnableConfig) -> dict:
    """Bounded tool-choice loop (`agent_max_tool_calls`) over documents,
    web and weather; any failure ends the loop and answers from what was
    gathered. Baseline entity context runs concurrently, awaited early
    only when `search_web` needs its log date.
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
        try:
            decision = await decide_research_tool(query, history, scratchpad)
            tool = decision.get("tool")
        except Exception:
            logger.warning("research tool-decision call failed, stopping loop", exc_info=True)
            break

        if not tool:
            break

        handler = _RESEARCH_TOOL_HANDLERS.get(tool)
        if handler is None:
            logger.warning("research tool-choice named an unrecognized tool %r", tool)
            break

        try:
            result = await handler(decision, ctx)
        except Exception:
            logger.warning("research tool dispatch failed for tool %r, stopping loop", tool, exc_info=True)
            break

        if result.tool_name is not None:
            tool_calls_made.append(result.tool_name)
        if result.scratchpad_entry:
            scratchpad.append(result.scratchpad_entry)
            _stream_status(result.scratchpad_entry)
        chunks.extend(result.chunks)
        web_citations.extend(result.citations)

    chunks = retrieval.dedup_chunks(chunks)
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


def _render_matched_entities(entities: list[dict]) -> str:
    if not entities:
        return "(none matched)"
    return "\n".join(f"- {e['id']}: {e['name']} ({e['domain']})" for e in entities)


async def _fetch_entities_for_action(cookie: str | None) -> list[dict]:
    """The household's entities, fetched once per action turn and shared
    with the baseline gather.
    """
    if cookie is None:
        return []
    return await entity_grounding.fetch_entities(cookie)


def _calendar(today: date) -> str:
    labels = {-1: " (yesterday)", 0: " (today)", 1: " (tomorrow)"}
    days = (today + timedelta(days=offset) for offset in range(-7, 15))
    return "\n".join(
        f"{day.strftime('%A')} {day.isoformat()}{labels.get((day - today).days, '')}"
        for day in days
    )


def _dated_message(query: str, today: date) -> dict:
    return {"role": "user", "content": f"Calendar:\n{_calendar(today)}\n\nUser message: {query}"}


def action_target_message(query: str, matched: list[dict]) -> dict:
    return {
        "role": "user",
        "content": (
            f"Household entities you can reference:\n{_render_matched_entities(matched)}"
            f"\n\nUser message: {query}"
        ),
    }


def _entity_label(entity: dict) -> str:
    return f"{entity['name']} ({entity['domain']})"


def _without_empty(fields: dict) -> dict:
    return {k: v for k, v in fields.items() if v is not None and v != {}}


def _validated_args(model: type[LogCreate] | type[ScheduleCreate], args: dict) -> dict:
    return model.model_validate(_without_empty(args)).model_dump(mode="json", exclude_unset=True)


def _log_summary(args: dict, entity: dict) -> str:
    return f"Log {args['type']}: {args['title']} for {entity['name']}, {args['occurred_at']}"


def _cadence(args: dict) -> str:
    kind = args["interval_type"]
    if kind == "time":
        return f"every {args['interval_days']} days"
    if kind == "usage":
        return f"every {args['interval_usage_amount']:g} ({args['usage_metric']})"
    if kind == "once":
        return f"on {args['planned_at']}"
    if "monthly_day" in args:
        return f"monthly on day {args['monthly_day']}"
    week = next(name for name, index in _WEEK_INDEXES.items() if index == args["monthly_week_index"])
    return f"monthly on the {week} {_WEEKDAYS[args['monthly_weekday']].capitalize()}"


def _schedule_summary(args: dict, entity: dict) -> str:
    summary = f"Schedule {args['title']} for {entity['name']}, {_cadence(args)}"
    if "starting_at" in args:
        summary += f", last done {args['starting_at']}"
    return summary


def _next_occurrence(month: int, day: int, year: int | None, today: date) -> date:
    if year is not None:
        return date(year, month, day)
    this_year = date(today.year, month, day)
    return this_year if this_year >= today else date(today.year + 1, month, day)


def _previous_occurrence(month: int, day: int, year: int | None, today: date) -> date:
    if year is not None:
        return date(year, month, day)
    this_year = date(today.year, month, day)
    return this_year if this_year <= today else date(today.year - 1, month, day)


def _schedule_detail_args(interval_type: str, detail: dict, today: date) -> dict:
    """Turns the model's plain answers (every 6 months, the last Friday)
    into ScheduleCreate's numeric fields; the arithmetic stays in code."""
    if interval_type == "time":
        return {
            "interval_days": detail["every"] * _UNIT_DAYS[detail["unit"]],
            "starting_at": detail.get("starting_at"),
        }
    if interval_type == "usage":
        return {
            "usage_metric": detail["usage_metric"],
            "interval_usage_amount": detail["interval_usage_amount"],
            "starting_usage_value": detail.get("starting_usage_value"),
        }
    if interval_type == "once":
        planned = _next_occurrence(detail["month"], detail["day"], detail.get("year"), today)
        return {"planned_at": planned.isoformat()}
    return {**_monthly_fields(detail["when"]), "starting_at": detail.get("starting_at")}


def _monthly_fields(when: str) -> dict:
    """Parses the model's quoted phrase ('first Saturday', '2nd Tues', 'the
    15th', 'the first') into exactly one day or weekday of the month.
    Raises rather than guess when the phrase names none, more than one
    ('2nd and 4th Tuesday'), or one a schedule can't hold ('fifth Friday',
    'second-to-last Friday', 'first business day')."""
    text = re.sub(r"\b(twenty|thirty)\s+", r"\1-", when.lower())
    if _UNREPRESENTABLE_DAYS.search(text):
        raise UnclearMonthlyDay(f"monthly phrase {when!r} names a day a schedule can't hold")
    weekday = _WEEKDAY_OF_MONTH.search(text)
    if weekday:
        fields = {
            "monthly_weekday": _WEEKDAY_WORDS[weekday.group(2)],
            "monthly_week_index": _WEEK_WORDS[weekday.group(1)],
        }
        match = weekday
    elif day := _DAY_OF_MONTH.search(text):
        fields, match = {"monthly_day": int(day.group(1))}, day
    elif ordinal := _ORDINAL_DAY_OF_MONTH.search(text):
        fields, match = {"monthly_day": _ORDINAL_DAYS[ordinal.group(1)]}, ordinal
    else:
        raise UnclearMonthlyDay(f"monthly phrase {when!r} names no day of the month")
    if _DAY_WORDS.search(text[: match.start()] + " " + text[match.end() :]):
        raise UnclearMonthlyDay(f"monthly phrase {when!r} names more than one day, or one it can't hold")
    return fields


async def _propose_log(query: str, entity: dict, today: date) -> ProposedAction | None:
    log_types = ENTITY_DOMAINS[entity["domain"]].LOG_TYPES
    type_definitions = "; ".join(f"'{t}': {LOG_TYPE_DESCRIPTIONS[t]}" for t in log_types)

    def _extract(fields: dict) -> ProposedAction:
        metrics = {str(k): str(v) for k, v in (fields.get("metrics") or {}).items()}
        args = _validated_args(
            LogCreate, {**fields, "metrics": metrics, "entity_id": entity["id"]}
        )
        if args["type"] not in log_types:
            raise ValueError(f"log type {args['type']!r} is not valid for {entity['domain']}")
        return {"tool": "create_log", "args": args, "summary": _log_summary(args, entity)}

    system = LOG_FIELDS_SYSTEM_PROMPT.format(entity=_entity_label(entity), log_types=type_definitions)
    return await ollama.complete_structured_or_default(
        [{"role": "system", "content": system}, _dated_message(query, today)],
        log_fields_schema(log_types),
        extract=_extract,
        default=None,
        warning="log-fields call failed or failed validation, degrading to no proposed action",
    )


async def _propose_schedule(query: str, entity: dict, today: date) -> ProposedAction | None:
    interval_types = "; ".join(f"'{k}': {v}" for k, v in INTERVAL_TYPE_DESCRIPTIONS.items())
    kind = await ollama.complete_structured_or_default(
        [
            {
                "role": "system",
                "content": SCHEDULE_KIND_SYSTEM_PROMPT.format(
                    entity=_entity_label(entity), interval_types=interval_types
                ),
            },
            {"role": "user", "content": query},
        ],
        SCHEDULE_KIND_SCHEMA,
        extract=lambda decision: (decision["interval_type"], decision["title"]),
        default=None,
        warning="schedule-kind call failed, degrading to no proposed action",
    )
    if kind is None or kind[0] not in SCHEDULE_DETAIL_SCHEMAS:
        return None
    interval_type, title = kind

    system = SCHEDULE_DETAIL_SYSTEM_PROMPTS[interval_type].format(
        title=title, entity=_entity_label(entity)
    )
    if interval_type in _DATED_INTERVAL_TYPES:
        user = _dated_message(query, today)
    else:
        user = {"role": "user", "content": query}
    detail = await ollama.complete_structured_or_default(
        [{"role": "system", "content": system}, user],
        SCHEDULE_DETAIL_SCHEMAS[interval_type],
        extract=dict,
        default=None,
        warning="schedule-detail call failed, degrading to no proposed action",
    )
    if detail is None:
        return None
    if interval_type == "monthly" and detail.get("last_done"):
        detail["starting_at"] = await _last_done_date(query, title, entity, detail["last_done"], today)
    try:
        detail_args = _schedule_detail_args(interval_type, detail, today)
        args = _validated_args(
            ScheduleCreate,
            {"entity_id": entity["id"], "title": title, "interval_type": interval_type, **detail_args},
        )
    except UnclearMonthlyDay as exc:
        logger.info("no schedule proposed: %s", exc)
        return None
    except Exception:
        logger.warning("schedule-detail answer failed validation, degrading to no proposed action", exc_info=True)
        return None
    return {"tool": "create_schedule", "args": args, "summary": _schedule_summary(args, entity)}


async def _last_done_date(query: str, title: str, entity: dict, last_done: str, today: date) -> str | None:
    """Turns the user's words for when a monthly task was last done into a
    date, with the calendar this step needs and the monthly step must not
    see. None on failure; the schedule is still proposed without it."""
    system = LAST_DONE_SYSTEM_PROMPT.format(title=title, entity=_entity_label(entity), last_done=last_done)
    return await ollama.complete_structured_or_default(
        [{"role": "system", "content": system}, _dated_message(query, today)],
        LAST_DONE_SCHEMA,
        extract=lambda answer: _previous_occurrence(
            answer["month"], answer["day"], answer.get("year"), today
        ).isoformat(),
        default=None,
        warning="last-done date call failed, proposing the schedule without a starting date",
    )


async def decide_action(query: str, matched: list[dict], today: date) -> ProposedAction | None:
    """The write path's model calls, one concern each: tool and entity, then
    that tool's fields. The entity can only be one of `matched`, code
    writes the summary, and args pass LogCreate/ScheduleCreate. Any
    failure is None.
    """
    if not matched:
        return None
    target = await ollama.complete_structured_or_default(
        [
            {"role": "system", "content": ACTION_TARGET_SYSTEM_PROMPT},
            action_target_message(query, matched),
        ],
        action_target_schema([e["id"] for e in matched]),
        extract=lambda decision: (decision.get("tool"), decision.get("entity_id")),
        default=(None, None),
        warning="action-target call failed, degrading to no proposed action",
    )
    tool, entity_id = target
    entity = next((e for e in matched if e["id"] == entity_id), None)
    if tool not in ACTION_TOOLS or entity is None or entity.get("domain") not in ENTITY_DOMAINS:
        return None
    if tool == "create_log":
        return await _propose_log(query, entity, today)
    return await _propose_schedule(query, entity, today)


async def propose_action_node(state: AgentState, config: RunnableConfig) -> dict:
    """Decides the action once (a resumed graph re-runs a node from the top,
    so the decision is kept out of `execute_action_node`). Baseline
    context gathers concurrently and is kept only when nothing is
    proposed, so a clarifying answer is still grounded. Dates count from
    `configurable["today"]` when set, else the container's date.
    """
    query = state["query"]
    cookie = config["configurable"].get("cookie")
    household_id = config["configurable"].get("household_id")
    entities = await _fetch_entities_for_action(cookie)
    matched = entity_grounding.find_matching_entities(query, entities, uncapped=True)
    baseline_matched = matched[: settings.entity_match_limit]
    baseline_task = asyncio.create_task(
        gather_baseline_context(query, cookie, household_id, entities, baseline_matched)
    )

    today = config["configurable"].get("today") or date.today()
    proposed_action = await decide_action(query, matched, today)

    if proposed_action is not None:
        baseline_task.cancel()
        try:
            await baseline_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "gather_baseline_context raised after being cancelled by a "
                "confident action decision", exc_info=True
            )
        return {"proposed_action": proposed_action, "persona": ACTION_PERSONA}

    try:
        entity_context, chunks, citation_list = await baseline_task
    except Exception:
        logger.warning(
            "gather_baseline_context failed while gathering fallback grounding "
            "for an unclear action decision", exc_info=True
        )
        entity_context, chunks, citation_list = [], [], []
    return {
        "proposed_action": None,
        "entity_context": entity_context,
        "chunks": chunks,
        "citation_list": citation_list,
        "persona": ACTION_PERSONA,
        "tool_calls_made": ["gather_household_context", "search_household_documents"],
    }


_ACTION_EXECUTORS: dict[str, Callable[[str, dict], Awaitable[dict]]] = {
    "create_log": lambda cookie, args: mcp_tools.create_log(cookie, args),
    "create_schedule": lambda cookie, args: mcp_tools.create_schedule(cookie, args),
}


async def execute_action_node(state: AgentState, config: RunnableConfig) -> dict:
    """Pauses for confirmation, then writes, records a cancellation, or
    records `unclear`; a failed write becomes `failed`, never an error.
    Skips the pause when nothing was proposed. Cheap to re-run on resume.
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
