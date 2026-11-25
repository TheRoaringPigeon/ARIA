import asyncio
import json
from datetime import date

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

import app.agents.nodes as nodes_module
import app.core_api_client as core_api_client_module
from app.agents.graph import build_graph_builder
from app.agents.nodes import (
    execute_action_node,
    general_node,
    maintenance_node,
    propose_action_node,
    research_node,
    supervisor_node,
    vehicle_node,
)
from app.agents.state import (
    ACTION_PERSONA,
    GENERAL_PERSONA,
    MAINTENANCE_PERSONA,
    RESEARCH_PERSONA,
    VEHICLE_PERSONA,
    AgentState,
)
from app.config import settings
from app.entity_grounding import EntityContext
from app.providers.weather import DailyForecast
from app.retrieval import RetrievedChunk
from tests.action_fakes import (
    LOG_OIL_CHANGE,
    OIL_CHANGE_ARGS,
    OIL_CHANGE_SUMMARY,
    reply_for,
)


async def _no_citations(cookie, chunks):
    return []

# --- supervisor_node -------------------------------------------------


async def test_supervisor_routes_on_each_real_agent_value(monkeypatch):
    """`format`-constrained decoding means the model can only ever answer
    with one of `VALID_AGENTS`'s own values — no more free-text extraction
    (prose like "Vehicle." or "I'd say maintenance", or a distinct
    stand-in word for "action" to dodge it colliding with ordinary prose
    containing "action") to test, since that whole failure mode is now
    structurally impossible (scaling-debt.md #15). This just confirms each
    real value round-trips through unchanged.
    """
    for agent in nodes_module.VALID_AGENTS:

        async def fake_complete(messages, schema, _agent=agent):
            return {"agent": _agent}

        monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
        result = await supervisor_node({"query": "q"}, {})
        assert result == {"selected_agent": agent}


async def test_supervisor_defaults_to_general_on_value_outside_valid_agents(monkeypatch):
    """Defensive fallback for a schema-valid-JSON-but-still-wrong value
    (shouldn't happen given the enum-constrained schema, but degrades
    rather than propagating a bogus `selected_agent` if it ever did).
    """

    async def fake_complete(messages, schema):
        return {"agent": "not-a-real-agent"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_defaults_to_general_on_malformed_reply(monkeypatch):
    async def fake_complete(messages, schema):
        raise json.JSONDecodeError("bad json", "not json at all", 0)

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_defaults_to_general_on_ollama_failure(monkeypatch):
    async def fake_complete(messages, schema):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_defaults_to_general_on_non_dict_reply(monkeypatch):
    """`complete_structured()`'s `format`-constrained decoding only
    guarantees valid JSON, not that it matches the schema's top-level
    object shape — a schema-valid but non-dict reply (e.g. bare `null`)
    trips `decision.get(...)` with an `AttributeError`, which must degrade
    the same as an unreachable Ollama or malformed JSON, not raise past
    `complete_structured_or_default` (caught in code review).
    """

    async def fake_complete(messages, schema):
        return None

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_includes_history_for_context(monkeypatch):
    """Regression test: a short follow-up ("what about Warner Robins?")
    carries no signal on its own — prior turns must reach the classifier
    so it can tell this is still about the same (weather-forecast) topic
    instead of falling through to "general" (caught in code review, after
    live use surfaced exactly this).
    """
    captured = {}

    async def fake_complete(messages, schema):
        captured["messages"] = messages
        return {"agent": "research"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    history = [
        {"role": "user", "content": "what's the weather forecast for Lizella"},
        {"role": "assistant", "content": "Here's the 16-day forecast for Lizella..."},
    ]
    result = await supervisor_node({"query": "what about Warner Robins?", "history": history}, {})

    assert result == {"selected_agent": "research"}
    assert history[0] in captured["messages"]
    assert history[1] in captured["messages"]
    assert captured["messages"][-1] == {"role": "user", "content": "what about Warner Robins?"}


async def test_supervisor_defaults_to_empty_history_when_absent(monkeypatch):
    captured = {}

    async def fake_complete(messages, schema):
        captured["messages"] = messages
        return {"agent": "general"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    await supervisor_node({"query": "q"}, {})

    assert captured["messages"] == [
        {"role": "system", "content": nodes_module._SUPERVISOR_SYSTEM_PROMPT},
        {"role": "user", "content": "q"},
    ]


# --- maintenance_node / vehicle_node -----------------------------------

_ENTITY = EntityContext(
    id="e1",
    domain="vehicle",
    name="Sienna",
    tags=["car"],
    specs={},
    attrs=None,
    logs=[],
    schedules=[],
)


_CHUNK = RetrievedChunk(
    text="x",
    mongo_document_id="d1",
    page_number=1,
    chunk_index=0,
    section_header=None,
    distance=0.1,
)


async def test_maintenance_node_gathers_entity_context_and_documents(monkeypatch):
    """Maintenance also searches documents now, not just entity records —
    an earlier version gave it entity-grounding only, which code review
    caught as a real regression (a maintenance-classified question about
    an uploaded manual used to always search it, pre-M7).
    """
    calls = []

    async def fake_gather(query, cookie):
        calls.append(("gather", query, cookie))
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        calls.append(("retrieve", query))
        return [_CHUNK]

    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await maintenance_node(
        {"query": "what's due"}, {"configurable": {"cookie": "abc"}}
    )

    assert result["entity_context"] == [_ENTITY]
    assert result["chunks"] == [_CHUNK]
    assert result["persona"] == MAINTENANCE_PERSONA
    assert ("gather", "what's due", "abc") in calls
    assert ("retrieve", "what's due") in calls


async def test_vehicle_node_gathers_entity_context_and_documents_with_vehicle_persona(
    monkeypatch,
):
    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        return [_CHUNK]

    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await vehicle_node(
        {"query": "sienna mileage"}, {"configurable": {"cookie": "abc"}}
    )

    assert result["entity_context"] == [_ENTITY]
    assert result["chunks"] == [_CHUNK]
    assert result["persona"] == VEHICLE_PERSONA


# --- general_node --------------------------------------------------------


async def test_general_node_calls_both_tools_concurrently(monkeypatch):
    async def fake_complete(messages, schema):
        return {"needs_grounding": True}

    calls = []

    async def fake_gather(query, cookie):
        calls.append("gather")
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        calls.append("retrieve")
        return [
            RetrievedChunk(
                text="x",
                mongo_document_id="d1",
                page_number=1,
                chunk_index=0,
                section_header=None,
                distance=0.1,
            )
        ]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await general_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert sorted(calls) == ["gather", "retrieve"]
    assert result["persona"] == GENERAL_PERSONA
    assert len(result["entity_context"]) == 1
    assert len(result["chunks"]) == 1


async def test_general_node_gate_says_no_skips_grounding_entirely(monkeypatch):
    """A purely conversational/meta question ("what were we talking
    about?") should never reach `retrieve_context`/`gather_entity_context`
    at all — no wasted Chroma round trip, and no chance of a noisy
    embedding-distance match surfacing an irrelevant citation (caught in
    code review).
    """

    async def fake_complete(messages, schema):
        return {"needs_grounding": False}

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("should never be called when the gate says no")

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fail_if_called)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fail_if_called)

    result = await general_node(
        {"query": "hey, what were we talking about?"}, {"configurable": {"cookie": "abc"}}
    )

    assert result == {
        "entity_context": [],
        "chunks": [],
        "citation_list": [],
        "persona": GENERAL_PERSONA,
        "tool_calls_made": ["grounding_gate"],
    }


async def test_general_node_gate_uses_its_own_prompt(monkeypatch):
    captured = {}

    async def fake_complete(messages, schema):
        captured["messages"] = messages
        return {"needs_grounding": False}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    await general_node({"query": "thanks!"}, {"configurable": {"cookie": "abc"}})

    assert captured["messages"] == [
        {"role": "system", "content": nodes_module._GENERAL_GROUNDING_GATE_PROMPT},
        {"role": "user", "content": "thanks!"},
    ]


async def test_general_node_gate_defaults_to_searching_on_malformed_reply(monkeypatch):
    """The gate is biased toward "yes" — a malformed reply must not skip
    grounding, since a false-negative skip (search was actually needed) is
    the regression this whole gate has to avoid reintroducing.
    """

    async def fake_complete(messages, schema):
        raise json.JSONDecodeError("bad json", "not json at all", 0)

    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await general_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["entity_context"] == [_ENTITY]


async def test_general_node_gate_defaults_to_searching_on_ollama_failure(monkeypatch):
    async def fake_complete(messages, schema):
        raise RuntimeError("ollama unreachable")

    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await general_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["entity_context"] == [_ENTITY]


async def test_general_node_gate_defaults_to_searching_on_non_dict_reply(monkeypatch):
    """Same non-dict-reply hazard as `supervisor_node` — the gate is
    biased toward "yes", so this must also degrade to searching rather
    than raising or wrongly skipping grounding (caught in code review).
    """

    async def fake_complete(messages, schema):
        return ["not", "a", "dict"]

    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await general_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["entity_context"] == [_ENTITY]


# --- research_node ---------------------------------------------------------


async def test_research_node_stops_when_no_tool_needed(monkeypatch):
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        return {'tool': None}

    async def fake_retrieve(query, household_id):
        raise AssertionError("should never be called")

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert call_count == 1
    assert result["chunks"] == []
    assert result["entity_context"] == [_ENTITY]
    assert result["persona"] == RESEARCH_PERSONA


async def test_research_node_bounded_loop_never_exceeds_max_tool_calls(monkeypatch):
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        return {'tool': 'search_household_documents', 'query': 'keeps searching'}

    async def fake_retrieve(query, household_id):
        # A distinct chunk per call — this test is about the loop's
        # iteration bound, not chunk dedup (see
        # `test_research_node_dedups_overlapping_chunks_across_searches`
        # for that), so results must not collide under dedup.
        return [
            RetrievedChunk(
                text="x",
                mongo_document_id="d1",
                page_number=1,
                chunk_index=call_count,
                section_header=None,
                distance=0.1,
            )
        ]

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert call_count == settings.agent_max_tool_calls
    assert len(result["chunks"]) == settings.agent_max_tool_calls
    # +1 for the always-present "gather_household_context" baseline entry.
    assert len(result["tool_calls_made"]) == settings.agent_max_tool_calls + 1


async def test_research_node_dedups_overlapping_chunks_across_searches(monkeypatch):
    """Regression test (caught in code review): two reformulated searches
    that both return the same top chunk used to be appended twice with no
    dedup, so the identical excerpt was rendered twice into the prompt.
    """
    call_count = 0
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 2)

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        return {'tool': 'search_household_documents', 'query': 'keeps searching'}

    async def fake_retrieve(query, household_id):
        # Same chunk every time — simulates two related/reformulated
        # queries both nearest-matching the same page.
        return [_CHUNK]

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert call_count == 2
    assert result["chunks"] == [_CHUNK]


async def test_research_node_malformed_json_ends_loop_without_raising(monkeypatch):
    """`complete_structured()` itself raises `json.JSONDecodeError` on a
    malformed reply (schema-constrained decoding means this should be rare
    in practice, but a transport-level hiccup could still produce one) —
    `research_node`'s loop must degrade by ending, not propagate it.
    """

    async def fake_complete(messages, schema):
        raise json.JSONDecodeError("bad json", "not json at all", 0)

    async def fake_retrieve(query, household_id):
        raise AssertionError("should never be called")

    async def fake_gather(query, cookie):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": None}})

    assert result["chunks"] == []


async def test_research_node_ollama_failure_degrades_instead_of_crashing(monkeypatch):
    """Regression test for the confirmed code-review finding: a tool-
    decision call failure used to be able to crash `research_node`
    uncaught when the parse step sat outside the try/except guarding the
    model call. `decision = await ollama.complete_structured(...)` and its
    downstream `decision.get("tool")` now both effectively degrade
    together — any failure from `complete_structured()` (network,
    malformed reply, anything) must end the loop instead of crashing it.
    """

    async def fake_complete(messages, schema):
        raise TypeError("null content")

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["chunks"] == []


async def test_research_node_non_dict_reply_ends_loop_without_raising(monkeypatch):
    """Schema-valid JSON that isn't a dict at all (e.g. bare `null`) trips
    `decision.get("tool")` with an `AttributeError` — the loop's own
    try/except must catch this alongside genuine parse/transport failures
    rather than letting it escape `research_node` (caught in code review).
    """

    async def fake_complete(messages, schema):
        return None

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["chunks"] == []
    assert result["entity_context"] == [_ENTITY]
    assert result["persona"] == RESEARCH_PERSONA


# --- _validated_location ----------------------------------------------------


def test_validated_location_accepts_place_named_in_query():
    assert nodes_module._validated_location("Lizella, GA", "weather in Lizella") == "Lizella, GA"


def test_validated_location_rejects_place_absent_from_query():
    assert nodes_module._validated_location("San Francisco", "what's the weather like") is None


def test_validated_location_rejects_none():
    assert nodes_module._validated_location(None, "what's the weather like") is None


def test_validated_location_rejects_blank_string():
    assert nodes_module._validated_location("", "what's the weather like") is None


# --- tool registries ---------------------------------------------------------


def test_research_tool_handlers_registry_has_all_four_tools():
    """Locks in the registry's keys — a typo'd/removed entry here would
    silently make that tool unreachable (falling into `research_node`'s
    "unrecognized tool" branch) with no other test necessarily catching it.
    """
    assert set(nodes_module._RESEARCH_TOOL_HANDLERS) == {
        "search_household_documents",
        "search_web",
        "get_weather",
        "get_weather_forecast",
    }


def test_action_executors_registry_has_both_tools():
    assert set(nodes_module._ACTION_EXECUTORS) == {"create_log", "create_schedule"}


async def test_tool_context_get_entity_context_caches_across_calls():
    """`ToolContext.get_entity_context()` must await `entity_context_task`
    at most once per turn and hand back the same cached result on every
    subsequent call, regardless of which handler asks for it first.
    """
    call_count = 0

    async def fake_gather():
        nonlocal call_count
        call_count += 1
        return [_ENTITY]

    ctx = nodes_module.ToolContext(
        query="q",
        cookie="abc",
        household_id="h1",
        entity_context_task=asyncio.create_task(fake_gather()),
    )

    first = await ctx.get_entity_context()
    second = await ctx.get_entity_context()

    assert call_count == 1
    assert first is second
    assert first == [_ENTITY]


# --- research_node: M10 search_web / get_weather ---------------------------


class _FakeSearchProvider:
    def __init__(self, results):
        self._results = results
        self.calls = []

    async def search(self, query, since=None):
        self.calls.append((query, since))
        return self._results


class _FakeWeatherProvider:
    def __init__(self, result=None, forecast_result=None):
        self._result = result
        self._forecast_result = forecast_result
        self.calls = []
        self.forecast_calls = []

    async def get_weather(self, location):
        self.calls.append(location)
        return self._result

    async def get_forecast(self, location, days=16):
        self.forecast_calls.append((location, days))
        return self._forecast_result


_JUN_ENTITY = EntityContext(
    id="e2",
    domain="person",
    name="Jun",
    tags=["friend"],
    specs={},
    attrs={"Company": "Acme Corp"},
    logs=[{"occurred_at": "2026-06-01", "type": "note", "title": "Jun mentioned her new job at Acme"}],
    schedules=[],
)


async def test_research_node_search_web_returns_citations_and_uses_since_cutoff(monkeypatch):
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'search_web', 'query': 'Acme Corp news'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return [_JUN_ENTITY]

    fake_search = _FakeSearchProvider(
        [nodes_module.SearchResult(title="Acme Corp raises funding", url="https://x/1", snippet="...", published_at="2026-07-01")]
    )

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_search_provider", lambda: fake_search)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node(
        {"query": "talking points for Jun"}, {"configurable": {"cookie": "abc"}}
    )

    assert fake_search.calls == [("Acme Corp news", date(2026, 6, 1))]
    assert "search_web" in result["tool_calls_made"]
    assert len(result["citation_list"]) == 1
    citation = result["citation_list"][0]
    assert citation.source_type == "web"
    assert citation.title == "Acme Corp raises funding"
    assert citation.url == "https://x/1"
    assert result["entity_context"] == [_JUN_ENTITY]


async def test_research_node_search_web_since_none_when_no_matched_entity_logs(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'search_web', 'query': 'weather news'}

    async def fake_gather(query, cookie):
        return []

    fake_search = _FakeSearchProvider([])

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_search_provider", lambda: fake_search)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert fake_search.calls == [("weather news", None)]


async def test_research_node_get_weather_uses_explicit_location(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather', 'location': 'Lizella, GA'}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(
        nodes_module.WeatherResult(location_label="Lizella, US", temperature_c=30.0, condition="clear sky", wind_kph=5.0)
    )

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    result = await research_node({"query": "weather in Lizella"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.calls == ["Lizella, GA"]
    assert "get_weather" in result["tool_calls_made"]
    citation = result["citation_list"][0]
    assert citation.source_type == "web"
    assert "Lizella, US" in citation.title
    assert "30.0" in citation.snippet


async def test_research_node_get_weather_falls_back_to_household_city(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather'}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(
        nodes_module.WeatherResult(location_label="Lizella, US", temperature_c=30.0, condition="clear sky", wind_kph=5.0)
    )

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": "Lizella, GA"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node({"query": "what's the weather"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.calls == ["Lizella, GA"]


async def test_research_node_get_weather_ignores_hallucinated_location(monkeypatch):
    """Regression test: the tool-choice model has a documented bias
    toward inventing a place (observed live: defaulting to "San
    Francisco") instead of actually omitting `location` when the user
    never named one — the household's real default city must win instead
    of the hallucinated guess (caught in code review).
    """
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather', 'location': 'San Francisco'}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(
        nodes_module.WeatherResult(location_label="Lizella, US", temperature_c=30.0, condition="clear sky", wind_kph=5.0)
    )

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": "Lizella, GA"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node(
        {"query": "will there be a dry spell coming up?"}, {"configurable": {"cookie": "abc"}}
    )

    assert fake_weather.calls == ["Lizella, GA"]


async def test_research_node_get_weather_forecast_ignores_hallucinated_location(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather_forecast', 'location': 'San Francisco'}

    async def fake_gather(query, cookie):
        return []

    forecast = nodes_module.ForecastResult(location_label="Lizella, US", days=[])
    fake_weather = _FakeWeatherProvider(forecast_result=forecast)

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": "Lizella, GA"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node(
        {"query": "first day in the future with 5 dry days"}, {"configurable": {"cookie": "abc"}}
    )

    assert fake_weather.forecast_calls == [("Lizella, GA", nodes_module.MAX_FORECAST_DAYS)]


async def test_research_node_get_weather_skips_lookup_when_no_location_available(monkeypatch):
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'get_weather'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(None)

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": None}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "what's the weather"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.calls == []
    assert "get_weather" not in result["tool_calls_made"]


async def test_research_node_get_weather_forecast_returns_citation(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather_forecast', 'location': 'Lizella, GA', 'days': 5}

    async def fake_gather(query, cookie):
        return []

    forecast = nodes_module.ForecastResult(
        location_label="Lizella, US",
        days=[
            DailyForecast(day=date(2026, 7, 21), precipitation_mm=0.0, condition="clear sky"),
            DailyForecast(day=date(2026, 7, 22), precipitation_mm=3.1, condition="slight rain"),
        ],
    )
    fake_weather = _FakeWeatherProvider(forecast_result=forecast)

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    result = await research_node(
        {"query": "will it be dry this week in Lizella"}, {"configurable": {"cookie": "abc"}}
    )

    assert fake_weather.forecast_calls == [("Lizella, GA", 5)]
    assert "get_weather_forecast" in result["tool_calls_made"]
    citation = result["citation_list"][0]
    assert citation.source_type == "web"
    assert "2-day forecast for Lizella, US" in citation.title
    assert "2026-07-21: clear sky, 0.0mm precipitation" in citation.snippet
    assert "2026-07-22: slight rain, 3.1mm precipitation" in citation.snippet


async def test_research_node_get_weather_forecast_falls_back_to_household_city(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather_forecast'}

    async def fake_gather(query, cookie):
        return []

    forecast = nodes_module.ForecastResult(location_label="Lizella, US", days=[])
    fake_weather = _FakeWeatherProvider(forecast_result=forecast)

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": "Lizella, GA"}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node({"query": "any dry days coming up"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.forecast_calls == [("Lizella, GA", nodes_module.MAX_FORECAST_DAYS)]


async def test_research_node_get_weather_forecast_skips_lookup_when_no_location_available(monkeypatch):
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'get_weather_forecast'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider()

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": None}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "any dry days coming up"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.forecast_calls == []
    assert "get_weather_forecast" not in result["tool_calls_made"]


async def test_research_node_get_weather_forecast_invalid_days_defaults_to_max(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'get_weather_forecast', 'location': 'Lizella, GA', 'days': 'not-a-number'}

    async def fake_gather(query, cookie):
        return []

    forecast = nodes_module.ForecastResult(location_label="Lizella, US", days=[])
    fake_weather = _FakeWeatherProvider(forecast_result=forecast)

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 1)

    await research_node({"query": "forecast for Lizella"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.forecast_calls == [("Lizella, GA", nodes_module.MAX_FORECAST_DAYS)]


async def test_research_node_weather_and_forecast_citations_not_wrongly_deduped(monkeypatch):
    """`get_weather` and `get_weather_forecast` citations both use the same
    static open-meteo.com URL — the dedup keyed on (url, title) must not
    collapse two genuinely different results just because they share a
    URL (caught in code review).
    """
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'get_weather', 'location': 'Lizella, GA'}
        if call_count == 2:
            return {'tool': 'get_weather_forecast', 'location': 'Lizella, GA'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(
        result=nodes_module.WeatherResult(
            location_label="Lizella, US", temperature_c=30.0, condition="clear sky", wind_kph=5.0
        ),
        forecast_result=nodes_module.ForecastResult(location_label="Lizella, US", days=[]),
    )

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 3)

    result = await research_node({"query": "weather in Lizella"}, {"configurable": {"cookie": "abc"}})

    assert len(result["citation_list"]) == 2


async def test_research_node_get_weather_treats_empty_string_city_as_no_location(monkeypatch):
    """An empty-string household `city` must be treated the same as "no
    default location set", not passed through to the weather provider
    (caught in code review).
    """
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'get_weather'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    fake_weather = _FakeWeatherProvider(None)

    async def fake_get_household(cookie):
        return {"id": "h1", "name": "H", "city": ""}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_weather_provider", lambda: fake_weather)
    monkeypatch.setattr(nodes_module.core_api_client, "get_household", fake_get_household)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "what's the weather"}, {"configurable": {"cookie": "abc"}})

    assert fake_weather.calls == []
    assert "get_weather" not in result["tool_calls_made"]


async def test_research_node_search_web_since_only_applied_to_first_call(monkeypatch):
    """A second, reformulated `search_web` call in the same turn must not
    reuse the first call's recency cutoff — the reformulation may be
    about an unrelated subject, and reapplying the cutoff could wrongly
    filter out relevant recent results (caught in code review).
    """
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'search_web', 'query': 'Acme Corp news'}
        if call_count == 2:
            return {'tool': 'search_web', 'query': 'unrelated topic'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return [_JUN_ENTITY]

    fake_search = _FakeSearchProvider([])

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_search_provider", lambda: fake_search)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 3)

    await research_node({"query": "talking points for Jun"}, {"configurable": {"cookie": "abc"}})

    assert fake_search.calls == [
        ("Acme Corp news", date(2026, 6, 1)),
        ("unrelated topic", None),
    ]


async def test_research_node_dedupes_overlapping_web_citations(monkeypatch):
    """Two `search_web` calls returning the same URL used to render as two
    duplicate citation pills (caught in code review) — same reasoning
    already applied to document chunk dedup.
    """
    call_count = 0

    async def fake_complete(messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {'tool': 'search_web', 'query': 'Acme Corp funding'}
        if call_count == 2:
            return {'tool': 'search_web', 'query': 'Acme Corp news'}
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    same_result = nodes_module.SearchResult(
        title="Acme Corp raises funding", url="https://x/1", snippet="..."
    )
    fake_search = _FakeSearchProvider([same_result])

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_search_provider", lambda: fake_search)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)
    monkeypatch.setattr(nodes_module.settings, "agent_max_tool_calls", 3)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert len(result["citation_list"]) == 1


async def test_research_node_search_web_provider_error_ends_loop_without_raising(monkeypatch):
    """`get_search_provider()` raises `ValueError` on a misconfigured
    `AI_SERVICE_SEARCH_PROVIDER` — this must degrade like every other
    failure in this loop, not escape `research_node` and discard whatever
    was already gathered this turn (caught in code review).
    """

    async def fake_complete(messages, schema):
        return {'tool': 'search_web', 'query': 'Acme Corp news'}

    async def fake_gather(query, cookie):
        return []

    def raise_misconfigured():
        raise ValueError("Unknown AI_SERVICE_SEARCH_PROVIDER 'bogus' — available: ['brave']")

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module, "get_search_provider", raise_misconfigured)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["citation_list"] == []
    assert result["persona"] == RESEARCH_PERSONA


async def test_research_node_unrecognized_tool_ends_loop(monkeypatch):
    async def fake_complete(messages, schema):
        return {'tool': 'fly_to_the_moon'}

    async def fake_gather(query, cookie):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["chunks"] == []
    assert result["citation_list"] == []


async def test_research_node_includes_history_in_tool_choice_messages(monkeypatch):
    """Regression test: the tool-choice loop used to see only the current
    turn's `query` — a follow-up like "what about Warner Robins?" gives
    the model no way to know it's still a weather-forecast question
    without the prior exchange (caught in code review).
    """
    captured = []

    async def fake_complete(messages, schema):
        captured.append(messages)
        return {'tool': None}

    async def fake_gather(query, cookie):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    history = [
        {"role": "user", "content": "what's the weather forecast for Lizella"},
        {"role": "assistant", "content": "Here's the 16-day forecast for Lizella..."},
    ]
    await research_node(
        {"query": "what about Warner Robins?", "history": history},
        {"configurable": {"cookie": "abc"}},
    )

    assert history[0] in captured[0]
    assert history[1] in captured[0]


# --- propose_action_node / execute_action_node ----------------------------

_VEHICLE_ENTITY = {"id": "e1", "name": "Ranger", "domain": "vehicle", "tags": []}


def _fake_action_model(replies, calls=None):
    async def fake_complete(messages, schema):
        if calls is not None:
            calls.append({"messages": messages, "schema": schema})
        return reply_for(schema, replies)

    return fake_complete


def _patch_action_io(monkeypatch, entities, replies, calls=None, gathered=None, chunks=None):
    async def fake_list_entities(cookie):
        return entities

    async def fake_gather(query, cookie, entities=None, matched=None):
        return gathered or []

    async def fake_retrieve(query, household_id):
        return chunks or []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete_structured", _fake_action_model(replies, calls))
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)


async def test_propose_action_node_confident_decision(monkeypatch):
    _patch_action_io(monkeypatch, [_VEHICLE_ENTITY], LOG_OIL_CHANGE)

    result = await propose_action_node(
        {"query": "log that I changed the oil on the Ranger today"},
        {"configurable": {"cookie": "abc"}},
    )

    assert result == {
        "proposed_action": {
            "tool": "create_log",
            "args": OIL_CHANGE_ARGS,
            "summary": OIL_CHANGE_SUMMARY,
        },
        "persona": ACTION_PERSONA,
    }


async def test_propose_action_node_stringifies_numeric_metrics(monkeypatch):
    replies = {
        **LOG_OIL_CHANGE,
        "log_fields": {**LOG_OIL_CHANGE["log_fields"], "metrics": {"mileage": 61500}},
    }
    _patch_action_io(monkeypatch, [_VEHICLE_ENTITY], replies)

    result = await propose_action_node(
        {"query": "log that I changed the oil on the Ranger at 61500 miles"},
        {"configurable": {"cookie": "abc"}},
    )

    assert result["proposed_action"]["args"]["metrics"] == {"mileage": "61500"}


async def test_propose_action_node_fetches_entities_only_once(monkeypatch):
    """`propose_action_node` must fetch the entity list once and pass it
    into `gather_baseline_context`, exercised against the real
    `gather_entity_context` with only its deeper I/O faked out."""
    list_entities_calls = []

    async def fake_list_entities(cookie):
        list_entities_calls.append(cookie)
        return [_VEHICLE_ENTITY]

    async def fake_list_entity_logs(cookie, entity_id, limit):
        return []

    async def fake_list_entity_schedules(cookie, entity_id):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )
    monkeypatch.setattr(
        nodes_module.ollama, "complete_structured", _fake_action_model(LOG_OIL_CHANGE)
    )
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    await propose_action_node(
        {"query": "log that I changed the oil on the Ranger today"},
        {"configurable": {"cookie": "abc"}},
    )

    assert list_entities_calls == ["abc"]


async def test_propose_action_node_caps_fallback_grounding_entities(monkeypatch):
    """The write-path match is uncapped so any matching entity is
    selectable, but the fallback grounding gather stays capped at
    `entity_match_limit`."""
    entities = [
        {"id": f"e{i}", "name": f"Car {i}", "domain": "vehicle", "tags": ["car"]}
        for i in range(5)
    ]
    captured = {}

    async def fake_gather(query, cookie, entities=None, matched=None):
        captured["matched"] = matched
        return []

    no_action = {"target": {"reason": "r", "tool": "none", "entity_id": None}}
    _patch_action_io(monkeypatch, entities, no_action)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(settings, "entity_match_limit", 2)

    await propose_action_node(
        {"query": "log something about the car"}, {"configurable": {"cookie": "abc"}}
    )

    assert len(captured["matched"]) == 2


async def test_propose_action_node_degrades_and_grounds_when_no_entity_matches(monkeypatch):
    calls = []
    _patch_action_io(
        monkeypatch,
        [_VEHICLE_ENTITY],
        LOG_OIL_CHANGE,
        calls=calls,
        gathered=[_ENTITY],
        chunks=[_CHUNK],
    )

    result = await propose_action_node(
        {"query": "log that I fixed the thing"}, {"configurable": {"cookie": "abc"}}
    )

    assert result["proposed_action"] is None
    assert result["persona"] == ACTION_PERSONA
    assert result["entity_context"] == [_ENTITY]
    assert result["chunks"] == [_CHUNK]
    assert calls == []


_TODAY = date(2026, 11, 25)
_FURNACE = {"id": "f1", "name": "Furnace", "domain": "home", "tags": []}
_MOM = {"id": "p1", "name": "Mom", "domain": "person", "tags": []}


async def _decide(monkeypatch, replies, matched, calls=None):
    monkeypatch.setattr(
        nodes_module.ollama, "complete_structured", _fake_action_model(replies, calls)
    )
    return await nodes_module.decide_action("a request", matched, _TODAY)


async def test_decide_action_restricts_entity_id_to_the_matched_entities(monkeypatch):
    calls = []
    await _decide(monkeypatch, LOG_OIL_CHANGE, [_VEHICLE_ENTITY, _FURNACE], calls)

    schema = calls[0]["schema"]
    assert schema["properties"]["entity_id"]["enum"] == ["e1", "f1", None]
    assert schema["required"][0] == "reason"


async def test_decide_action_narrows_log_types_to_the_entity_domain(monkeypatch):
    calls = []
    replies = {
        "target": {"reason": "r", "tool": "create_log", "entity_id": "p1"},
        "log_fields": {**LOG_OIL_CHANGE["log_fields"], "type": "call", "title": "Called Mom"},
    }
    proposed = await _decide(monkeypatch, replies, [_MOM], calls)

    assert calls[1]["schema"]["properties"]["type"]["enum"] == list(
        nodes_module.ENTITY_DOMAINS["person"].LOG_TYPES
    )
    assert proposed["args"]["type"] == "call"


async def test_decide_action_proposes_nothing_when_the_model_answers_none(monkeypatch):
    replies = {"target": {"reason": "a question", "tool": "none", "entity_id": None}}
    assert await _decide(monkeypatch, replies, [_VEHICLE_ENTITY]) is None


async def test_decide_action_makes_no_model_call_without_a_matched_entity(monkeypatch):
    calls = []
    assert await _decide(monkeypatch, LOG_OIL_CHANGE, [], calls) is None
    assert calls == []


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "maintenance"},
        {"type": "call"},
        {"occurred_at": "last tuesday"},
        {"occurred_at": "2026-02-30"},
    ],
)
async def test_decide_action_drops_log_fields_that_fail_validation(monkeypatch, fields):
    replies = {**LOG_OIL_CHANGE, "log_fields": {**LOG_OIL_CHANGE["log_fields"], **fields}}
    assert await _decide(monkeypatch, replies, [_VEHICLE_ENTITY]) is None


async def test_decide_action_degrades_when_the_model_is_unreachable(monkeypatch):
    async def fake_complete(messages, schema):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    assert await nodes_module.decide_action("q", [_VEHICLE_ENTITY], _TODAY) is None


async def test_decide_action_gives_the_log_call_a_calendar(monkeypatch):
    calls = []
    await _decide(monkeypatch, LOG_OIL_CHANGE, [_VEHICLE_ENTITY], calls)

    content = calls[1]["messages"][-1]["content"]
    assert "Tuesday 2026-11-24 (yesterday)" in content
    assert "Wednesday 2026-11-25 (today)" in content


def _schedule_replies(interval_type, detail, entity_id="f1"):
    return {
        "target": {"reason": "r", "tool": "create_schedule", "entity_id": entity_id},
        "schedule_kind": {"reason": "r", "interval_type": interval_type, "title": "Replace filter"},
        "schedule_detail": detail,
    }


@pytest.mark.parametrize(
    ("interval_type", "detail", "expected_args", "cadence"),
    [
        (
            "time",
            {"every": 3, "unit": "months", "starting_at": None},
            {"interval_days": 90},
            "every 90 days",
        ),
        (
            "time",
            {"every": 2, "unit": "weeks", "starting_at": "2026-11-20"},
            {"interval_days": 14, "starting_at": "2026-11-20"},
            "every 14 days",
        ),
        (
            "usage",
            {"usage_metric": "hours", "interval_usage_amount": 250, "starting_usage_value": None},
            {"usage_metric": "hours", "interval_usage_amount": 250.0},
            "every 250 (hours)",
        ),
        (
            "once",
            {"month": 12, "day": 24, "year": None},
            {"planned_at": "2026-12-24"},
            "on 2026-12-24",
        ),
        (
            "once",
            {"month": 3, "day": 1, "year": None},
            {"planned_at": "2027-03-01"},
            "on 2027-03-01",
        ),
        (
            "once",
            {"month": 11, "day": 25, "year": None},
            {"planned_at": "2026-11-25"},
            "on 2026-11-25",
        ),
        (
            "once",
            {"month": 3, "day": 1, "year": 2028},
            {"planned_at": "2028-03-01"},
            "on 2028-03-01",
        ),
        (
            "monthly",
            {"when": "the 15th"},
            {"monthly_day": 15},
            "monthly on day 15",
        ),
        (
            "monthly",
            {"when": "first Saturday"},
            {"monthly_weekday": 5, "monthly_week_index": 1},
            "monthly on the first Saturday",
        ),
        (
            "monthly",
            {"when": "last Friday"},
            {"monthly_weekday": 4, "monthly_week_index": -1},
            "monthly on the last Friday",
        ),
        (
            "monthly",
            {"when": "2nd Tues"},
            {"monthly_weekday": 1, "monthly_week_index": 2},
            "monthly on the second Tuesday",
        ),
        (
            "monthly",
            {"when": "1st Sat"},
            {"monthly_weekday": 5, "monthly_week_index": 1},
            "monthly on the first Saturday",
        ),
        (
            "monthly",
            {"when": "the first of the month"},
            {"monthly_day": 1},
            "monthly on day 1",
        ),
        (
            "monthly",
            {"when": "Fifteenth"},
            {"monthly_day": 15},
            "monthly on day 15",
        ),
        (
            "monthly",
            {"when": "twenty first"},
            {"monthly_day": 21},
            "monthly on day 21",
        ),
        (
            "monthly",
            {"when": "the thirty-first"},
            {"monthly_day": 31},
            "monthly on day 31",
        ),
    ],
)
async def test_decide_action_turns_schedule_answers_into_schedule_fields(
    monkeypatch, interval_type, detail, expected_args, cadence
):
    proposed = await _decide(monkeypatch, _schedule_replies(interval_type, detail), [_FURNACE])

    assert proposed == {
        "tool": "create_schedule",
        "args": {
            "entity_id": "f1",
            "title": "Replace filter",
            "interval_type": interval_type,
            **expected_args,
        },
        "summary": f"Schedule Replace filter for Furnace, {cadence}",
    }


@pytest.mark.parametrize(
    ("interval_type", "detail"),
    [
        ("once", {"month": 2, "day": 30, "year": None}),
        ("monthly", {"when": "every month"}),
        ("monthly", {"when": "the 32nd"}),
    ],
)
async def test_decide_action_drops_schedule_details_that_fail_validation(
    monkeypatch, interval_type, detail
):
    proposed = await _decide(monkeypatch, _schedule_replies(interval_type, detail), [_FURNACE])
    assert proposed is None


@pytest.mark.parametrize(
    ("interval_type", "detail", "gets_calendar"),
    [
        ("once", {"month": 12, "day": 24, "year": None}, True),
        ("time", {"every": 90, "unit": "days", "starting_at": None}, True),
        ("monthly", {"when": "first Saturday"}, False),
        ("usage", {"usage_metric": "hours", "interval_usage_amount": 50, "starting_usage_value": None}, False),
    ],
)
async def test_decide_action_sends_a_calendar_only_to_date_answers(
    monkeypatch, interval_type, detail, gets_calendar
):
    calls = []
    await _decide(monkeypatch, _schedule_replies(interval_type, detail), [_FURNACE], calls)

    assert ("Calendar:" in calls[2]["messages"][-1]["content"]) is gets_calendar


async def test_decide_action_sends_each_schedule_type_its_own_detail_schema(monkeypatch):
    calls = []
    detail = {"usage_metric": "mileage", "interval_usage_amount": 5000, "starting_usage_value": None}
    await _decide(monkeypatch, _schedule_replies("usage", detail, "e1"), [_VEHICLE_ENTITY], calls)

    assert calls[2]["schema"] == nodes_module.SCHEDULE_DETAIL_SCHEMAS["usage"]
    assert len(calls) == 3


async def test_propose_action_node_malformed_json_degrades(monkeypatch):
    async def fake_list_entities(cookie):
        return []

    async def fake_complete(messages, schema):
        return "not json at all"

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await propose_action_node({"query": "q"}, {"configurable": {"cookie": None}})

    assert result["proposed_action"] is None
    assert result["persona"] == ACTION_PERSONA


async def test_propose_action_node_ollama_failure_degrades(monkeypatch):
    async def fake_list_entities(cookie):
        return []

    async def fake_complete(messages, schema):
        raise RuntimeError("ollama unreachable")

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await propose_action_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["proposed_action"] is None


async def _run_execute_action_node(initial_state: dict, cookie: str | None, resume_decision):
    """Drives `execute_action_node` through a real interrupt/resume cycle
    in isolation, on a minimal single-node graph of its own.

    `execute_action_node` calls `interrupt()` directly now (merged with
    what used to be a separate `confirm_gate_node` — caught in code
    review: the split bought nothing this node alone doesn't already
    provide), which requires an active LangGraph task context to work at
    all — calling it as a bare function, the way the pre-merge tests
    exercised the "confirm"/"reject" branches with a hand-set `decision`
    key, no longer works. A tiny standalone graph gives `interrupt()` that
    context without going through `propose_action_node`'s own LLM-driven
    tool whitelist, so tests can still exercise `proposed_action` shapes
    `propose_action_node` itself could never produce today (see the
    "unrecognized tool" test below).
    """
    builder = StateGraph(AgentState)
    builder.add_node("execute_action", execute_action_node)
    builder.set_entry_point("execute_action")
    builder.add_edge("execute_action", END)
    graph = builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"cookie": cookie, "thread_id": "t-execute-action-test"}}

    async for _ in graph.astream(initial_state, config, stream_mode="values"):
        pass

    final = None
    async for update in graph.astream(Command(resume=resume_decision), config, stream_mode="values"):
        final = update
    return final


async def test_execute_action_node_confirm_calls_matching_tool(monkeypatch):
    calls = []

    async def fake_create_schedule(cookie, args):
        calls.append((cookie, args))
        return {"id": "s1", **args}

    monkeypatch.setattr(nodes_module.mcp_tools, "create_schedule", fake_create_schedule)

    proposed = {
        "tool": "create_schedule",
        "args": {
            "entity_id": "e1",
            "title": "Rotate tires",
            "interval_type": "time",
            "interval_days": 180,
        },
        "summary": "Remind to rotate tires every 6 months",
    }
    final = await _run_execute_action_node(
        {"query": "q", "proposed_action": proposed}, "abc", "confirm"
    )

    assert final["action_result"] == {"status": "done", "summary": proposed["summary"]}
    assert calls == [("abc", proposed["args"])]


async def test_execute_action_node_reject_records_cancellation():
    proposed = {"tool": "create_log", "args": {}, "summary": "Log an oil change"}
    final = await _run_execute_action_node(
        {"query": "q", "proposed_action": proposed}, None, "reject"
    )
    assert final["action_result"] == {"status": "cancelled", "summary": proposed["summary"]}


async def test_execute_action_node_no_proposed_action_records_unclear():
    result = await execute_action_node({"proposed_action": None}, {"configurable": {}})
    assert result == {"action_result": {"status": "unclear"}}


async def test_execute_action_node_unrecognized_tool_records_unclear_instead_of_writing(
    monkeypatch,
):
    """An unrecognized `tool` value must not silently fall through to
    `create_schedule` (an earlier version's bare `if/else` did exactly
    that — caught in code review). Can't happen via `propose_action_node`
    today, but `proposed_action` round-trips through the checkpointer
    across the interrupt/resume cycle, so this is defense against a future
    third tool being added to the decision prompt without this dispatch
    also being updated.
    """

    async def unexpected_create_schedule(cookie, args):
        raise AssertionError("must not write for an unrecognized tool")

    monkeypatch.setattr(nodes_module.mcp_tools, "create_schedule", unexpected_create_schedule)

    proposed = {"tool": "delete_entity", "args": {}, "summary": "..."}
    final = await _run_execute_action_node(
        {"query": "q", "proposed_action": proposed}, "abc", "confirm"
    )

    assert final["action_result"] == {"status": "unclear"}


async def test_execute_action_node_surfaces_core_api_validation_error(monkeypatch):
    """A 400 from core-api's own ScheduleCreate validator (e.g. a missing
    interval-specific field) must surface its real detail message, not a
    generic failure — this is an explicit user-requested action, not
    passive grounding, so the failure can't be silently swallowed.
    """
    request = httpx.Request("POST", "http://core-api:8000/schedules")
    response = httpx.Response(400, json={"detail": "interval_days is required"}, request=request)
    error = httpx.HTTPStatusError("bad request", request=request, response=response)

    async def failing_create_schedule(cookie, args):
        raise error

    monkeypatch.setattr(nodes_module.mcp_tools, "create_schedule", failing_create_schedule)

    proposed = {
        "tool": "create_schedule",
        "args": {"entity_id": "e1", "title": "x", "interval_type": "time"},
        "summary": "...",
    }
    final = await _run_execute_action_node(
        {"query": "q", "proposed_action": proposed}, "abc", "confirm"
    )

    assert final["action_result"] == {"status": "failed", "detail": "interval_days is required"}


async def test_execute_action_node_extracts_message_from_fastapi_422_detail_list(monkeypatch):
    """`ScheduleCreate`'s own cross-field validator raises during FastAPI's
    request-body parsing, before core-api's handler ever runs — a real,
    live-verified 422 shape (`{"detail": [{"msg": "...", ...}, ...]}`),
    distinct from the handler's own plain-string 400 covered above. Both
    must surface the real reason, not a dict/list repr.
    """
    request = httpx.Request("POST", "http://core-api:8000/schedules")
    response = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body"],
                    "msg": "Value error, interval_days is required when interval_type is 'time'",
                },
                {
                    "type": "value_error",
                    "loc": ["body"],
                    "msg": "Value error, interval_days is required when interval_type is 'time'",
                },
            ]
        },
        request=request,
    )
    error = httpx.HTTPStatusError("unprocessable", request=request, response=response)

    async def failing_create_schedule(cookie, args):
        raise error

    monkeypatch.setattr(nodes_module.mcp_tools, "create_schedule", failing_create_schedule)

    proposed = {
        "tool": "create_schedule",
        "args": {"entity_id": "e1", "title": "x", "interval_type": "time"},
        "summary": "...",
    }
    final = await _run_execute_action_node(
        {"query": "q", "proposed_action": proposed}, "abc", "confirm"
    )

    assert final["action_result"] == {
        "status": "failed",
        "detail": "Value error, interval_days is required when interval_type is 'time'",
    }


async def test_graph_action_flow_interrupts_then_resume_confirms_without_repeating_llm_call(
    monkeypatch,
):
    """End-to-end through the real graph shape (MemorySaver, not Redis):
    routes to "action", proposes, interrupts, and — the whole reason for
    the two-node split — resuming the interrupt must NOT repeat
    `propose_action_node`'s LLM decision call, even though LangGraph
    re-runs `execute_action_node` itself from the top on resume.
    """
    calls = {"decision": 0}

    async def fake_complete(messages, schema):
        if "agent" not in schema["properties"]:
            calls["decision"] += 1
        return reply_for(schema, LOG_OIL_CHANGE)

    async def fake_list_entities(cookie):
        return [_VEHICLE_ENTITY]

    async def fake_create_log(cookie, args):
        return {"id": "log1", **args}

    # A confident decision cancels the concurrently-kicked-off baseline
    # gather rather than awaiting it (see `propose_action_node`) — but
    # cancellation timing isn't guaranteed to win the race before these
    # would otherwise attempt a real network call, so they're mocked
    # regardless, same as every other graph-level test in this file.
    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.mcp_tools, "create_log", fake_create_log)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": "abc", "thread_id": "t-action-1"}}

    final = None
    async for update in graph.astream(
        {"query": "log that I changed the oil on the Ranger today"}, config, stream_mode="values"
    ):
        final = update

    assert "__interrupt__" in final
    assert final["proposed_action"]["tool"] == "create_log"
    assert calls["decision"] == 2

    final2 = None
    async for update in graph.astream(Command(resume="confirm"), config, stream_mode="values"):
        final2 = update

    assert "__interrupt__" not in final2
    assert final2["action_result"] == {"status": "done", "summary": OIL_CHANGE_SUMMARY}
    assert calls["decision"] == 2


async def test_graph_action_flow_reject_records_cancellation_without_writing(monkeypatch):
    async def fake_complete(messages, schema):
        return reply_for(schema, LOG_OIL_CHANGE)

    async def fake_list_entities(cookie):
        return [_VEHICLE_ENTITY]

    async def failing_create_log(cookie, args):
        raise AssertionError("must not write when the user rejects the proposal")

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.mcp_tools, "create_log", failing_create_log)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": "abc", "thread_id": "t-action-2"}}

    async for _ in graph.astream(
        {"query": "log that I changed the oil on the Ranger"}, config, stream_mode="values"
    ):
        pass

    final = None
    async for update in graph.astream(Command(resume="reject"), config, stream_mode="values"):
        final = update

    assert final["action_result"] == {"status": "cancelled", "summary": OIL_CHANGE_SUMMARY}


# --- graph-level integration (MemorySaver, not Redis) -----------------------


async def _build_test_graph():
    return build_graph_builder().compile(checkpointer=MemorySaver())


async def test_graph_routes_vehicle_query_to_vehicle_specialist(monkeypatch):
    async def fake_complete(messages, schema):
        return {"agent": "vehicle"}

    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query, household_id):
        return [_CHUNK]

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": None, "thread_id": "t1"}}
    result = await graph.ainvoke({"query": "when's the Sienna's next service"}, config)

    assert result["selected_agent"] == "vehicle"
    assert result["persona"] == VEHICLE_PERSONA
    assert result["entity_context"] == [_ENTITY]
    assert result["chunks"] == [_CHUNK]


async def test_graph_routes_research_query_to_research_assistant(monkeypatch):
    async def fake_complete(messages, schema):
        # First call is the supervisor's classification, second is the
        # research node's tool-decision — distinguish by prompt content.
        if "routing classifier" in messages[0]["content"]:
            return {"agent": "research"}
        return {'tool': None}

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": None, "thread_id": "t2"}}
    result = await graph.ainvoke({"query": "what does the manual say"}, config)

    assert result["selected_agent"] == "research"
    assert result["persona"] == RESEARCH_PERSONA


async def test_graph_falls_back_to_general_on_unroutable_query(monkeypatch):
    async def fake_complete(messages, schema):
        return {"agent": "not a real category"} if "routing classifier" in messages[0]["content"] else {"needs_grounding": True}

    async def fake_gather(query, cookie):
        return []

    async def fake_retrieve(query, household_id):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete_structured", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": None, "thread_id": "t3"}}
    result = await graph.ainvoke({"query": "tell me a joke"}, config)

    assert result["selected_agent"] == "general"
    assert result["persona"] == GENERAL_PERSONA
