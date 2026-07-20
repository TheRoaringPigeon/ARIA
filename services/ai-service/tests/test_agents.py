import httpx
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
from app.retrieval import RetrievedChunk


async def _no_citations(cookie, chunks):
    return []

# --- supervisor_node -------------------------------------------------


async def test_supervisor_routes_on_clean_one_word_response(monkeypatch):
    async def fake_complete(messages):
        return "vehicle"

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    result = await supervisor_node({"query": "when's the Sienna due for service"}, {})

    assert result == {"selected_agent": "vehicle"}


async def test_supervisor_matches_label_even_with_extra_text(monkeypatch):
    for raw, expected in [
        ("Vehicle.", "vehicle"),
        ("I'd say maintenance", "maintenance"),
        ("RESEARCH", "research"),
    ]:

        async def fake_complete(messages, _raw=raw):
            return _raw

        monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
        result = await supervisor_node({"query": "q"}, {})
        assert result == {"selected_agent": expected}


async def test_supervisor_routes_to_action_on_its_distinct_classifier_word(monkeypatch):
    async def fake_complete(messages):
        return "log_or_schedule"

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    result = await supervisor_node({"query": "log the oil change"}, {})

    assert result == {"selected_agent": "action"}


async def test_supervisor_not_misrouted_by_the_word_action_in_ordinary_prose(monkeypatch):
    """`action` used to be the literal word the model was asked to answer
    with for this category — but `ModelAdapter.parse_choice()` picks
    whichever candidate word appears *earliest* in a verbose reply, and
    "action" is common enough in ordinary prose that a reply explaining
    *why* something is a general question (not asking for an action) could
    false-positive into the action/write path (caught in code review).
    Asking for the more distinctive "log_or_schedule" instead means this
    reply no longer contains any candidate word but "general".
    """

    async def fake_complete(messages):
        return "This doesn't need any action, it's a general question."

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_defaults_to_general_on_unparseable_response(monkeypatch):
    async def fake_complete(messages):
        return "I have no idea what category that is"

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


async def test_supervisor_defaults_to_general_on_ollama_failure(monkeypatch):
    async def fake_complete(messages):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    result = await supervisor_node({"query": "q"}, {})

    assert result == {"selected_agent": "general"}


# --- maintenance_node / vehicle_node -----------------------------------

_ENTITY = EntityContext(
    id="e1",
    domain="vehicle",
    name="Sienna",
    tags=["car"],
    specs={},
    person_attrs=None,
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

    async def fake_retrieve(query):
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

    async def fake_retrieve(query):
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
    calls = []

    async def fake_gather(query, cookie):
        calls.append("gather")
        return [_ENTITY]

    async def fake_retrieve(query):
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

    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await general_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert sorted(calls) == ["gather", "retrieve"]
    assert result["persona"] == GENERAL_PERSONA
    assert len(result["entity_context"]) == 1
    assert len(result["chunks"]) == 1


# --- research_node ---------------------------------------------------------


async def test_research_node_stops_when_no_tool_needed(monkeypatch):
    call_count = 0

    async def fake_complete(messages):
        nonlocal call_count
        call_count += 1
        return '{"tool": null}'

    async def fake_retrieve(query):
        raise AssertionError("should never be called")

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert call_count == 1
    assert result["chunks"] == []
    assert result["entity_context"] == [_ENTITY]
    assert result["persona"] == RESEARCH_PERSONA


async def test_research_node_bounded_loop_never_exceeds_max_tool_calls(monkeypatch):
    call_count = 0

    async def fake_complete(messages):
        nonlocal call_count
        call_count += 1
        return '{"tool": "search_household_documents", "query": "keeps searching"}'

    async def fake_retrieve(query):
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

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
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

    async def fake_complete(messages):
        nonlocal call_count
        call_count += 1
        return '{"tool": "search_household_documents", "query": "keeps searching"}'

    async def fake_retrieve(query):
        # Same chunk every time — simulates two related/reformulated
        # queries both nearest-matching the same page.
        return [_CHUNK]

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert call_count == 2
    assert result["chunks"] == [_CHUNK]


async def test_research_node_malformed_json_ends_loop_without_raising(monkeypatch):
    async def fake_complete(messages):
        return "not json at all"

    async def fake_retrieve(query):
        raise AssertionError("should never be called")

    async def fake_gather(query, cookie):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": None}})

    assert result["chunks"] == []


async def test_research_node_null_content_degrades_instead_of_crashing(monkeypatch):
    """Regression test for the confirmed code-review finding: a `None`
    (not missing, just null) `content` from `ollama.complete()` used to
    crash `research_node` uncaught, because the tool-decision parse call
    sat outside the try/except that guarded the model call. It must
    degrade the loop instead, same as any other parse failure.
    """

    async def fake_complete(messages):
        return None

    async def fake_gather(query, cookie):
        return [_ENTITY]

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)

    result = await research_node({"query": "q"}, {"configurable": {"cookie": "abc"}})

    assert result["chunks"] == []
    assert result["entity_context"] == [_ENTITY]
    assert result["persona"] == RESEARCH_PERSONA


# --- propose_action_node / execute_action_node ----------------------------

_VEHICLE_ENTITY = {"id": "e1", "name": "Ranger", "domain": "vehicle", "tags": []}


async def test_propose_action_node_confident_decision(monkeypatch):
    async def fake_list_entities(cookie):
        return [_VEHICLE_ENTITY]

    async def fake_complete(messages):
        return (
            '{"tool": "create_log", "args": {"entity_id": "e1", "type": "service", '
            '"occurred_at": "2026-07-18", "title": "Oil change"}, '
            '"summary": "Log an oil change for the Ranger today"}'
        )

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await propose_action_node(
        {"query": "log that I changed the oil on the Ranger today"},
        {"configurable": {"cookie": "abc"}},
    )

    assert result == {
        "proposed_action": {
            "tool": "create_log",
            "args": {
                "entity_id": "e1",
                "type": "service",
                "occurred_at": "2026-07-18",
                "title": "Oil change",
            },
            "summary": "Log an oil change for the Ranger today",
        },
        "persona": ACTION_PERSONA,
    }


async def test_propose_action_node_fetches_entities_only_once(monkeypatch):
    """An earlier version fetched the household's entity list twice per
    action-classified turn — once for the decision prompt's matching, once
    again (concurrently) inside `gather_baseline_context`'s own
    `entity_grounding.gather_entity_context` call — doubling `GET
    /entities` load on core-api for no behavioral benefit (caught in code
    review). `propose_action_node` must fetch it once and pass the result
    into `gather_baseline_context` so `gather_entity_context` reuses it
    instead of re-fetching — exercised here against the real
    `gather_entity_context` (not a full-function mock of it, which would
    hide this exact bug), with only its own deeper I/O
    (`list_entity_logs`/`list_entity_schedules`) faked out.
    """
    list_entities_calls = []

    async def fake_list_entities(cookie):
        list_entities_calls.append(cookie)
        return [_VEHICLE_ENTITY]

    async def fake_list_entity_logs(cookie, entity_id):
        return []

    async def fake_list_entity_schedules(cookie, entity_id):
        return []

    async def fake_complete(messages):
        return (
            '{"tool": "create_log", "args": {"entity_id": "e1", "type": "service", '
            '"occurred_at": "2026-07-18", "title": "Oil change"}, '
            '"summary": "Log an oil change for the Ranger today"}'
        )

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    await propose_action_node(
        {"query": "log that I changed the oil on the Ranger today"},
        {"configurable": {"cookie": "abc"}},
    )

    assert list_entities_calls == ["abc"]


async def test_propose_action_node_caps_fallback_grounding_entities(monkeypatch):
    """The write-path whitelist match (`matched`, used for the action
    prompt and `matched_ids`) is deliberately uncapped so a real entity
    beyond `entity_match_limit` is still selectable — but the *fallback*
    grounding call made when no action can be confidently proposed must
    stay capped like every other specialist's baseline gather, or a broad
    query term matching many entities turns a plain clarifying-question
    turn into an unbounded core-api fan-out (caught in code review: this
    used to pass the uncapped list straight through).
    """
    entities = [
        {"id": f"e{i}", "name": f"Car {i}", "domain": "vehicle", "tags": ["car"]}
        for i in range(5)
    ]

    async def fake_list_entities(cookie):
        return entities

    async def fake_complete(messages):
        return '{"tool": null}'

    captured = {}

    async def fake_gather(query, cookie, entities=None, matched=None):
        captured["matched"] = matched
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(settings, "entity_match_limit", 2)
    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    await propose_action_node(
        {"query": "log something about the car"}, {"configurable": {"cookie": "abc"}}
    )

    assert len(captured["matched"]) == 2


async def test_propose_action_node_degrades_when_entity_unresolvable(monkeypatch):
    """The model naming an entity_id outside the household's matched list
    (or no entity matching the query at all) must degrade to no proposed
    action, same as unparseable JSON — and still gather baseline context so
    a clarifying-question answer has something to work with.
    """

    async def fake_list_entities(cookie):
        return [_VEHICLE_ENTITY]

    async def fake_complete(messages):
        return (
            '{"tool": "create_log", "args": {"entity_id": "does-not-exist", '
            '"type": "service", "occurred_at": "2026-07-18", "title": "x"}, '
            '"summary": "..."}'
        )

    async def fake_gather(query, cookie, entities=None, matched=None):
        return [_ENTITY]

    async def fake_retrieve(query):
        return [_CHUNK]

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await propose_action_node(
        {"query": "log that I fixed the thing"}, {"configurable": {"cookie": "abc"}}
    )

    assert result["proposed_action"] is None
    assert result["persona"] == ACTION_PERSONA
    assert result["entity_context"] == [_ENTITY]
    assert result["chunks"] == [_CHUNK]


async def test_propose_action_node_malformed_json_degrades(monkeypatch):
    async def fake_list_entities(cookie):
        return []

    async def fake_complete(messages):
        return "not json at all"

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(nodes_module.citations_module, "resolve_citations", _no_citations)

    result = await propose_action_node({"query": "q"}, {"configurable": {"cookie": None}})

    assert result["proposed_action"] is None
    assert result["persona"] == ACTION_PERSONA


async def test_propose_action_node_ollama_failure_degrades(monkeypatch):
    async def fake_list_entities(cookie):
        return []

    async def fake_complete(messages):
        raise RuntimeError("ollama unreachable")

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
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

    async def fake_complete(messages):
        if "routing classifier" in messages[0]["content"]:
            return "log_or_schedule"
        calls["decision"] += 1
        return (
            '{"tool": "create_log", "args": {"entity_id": "e1", "type": "service", '
            '"occurred_at": "2026-07-18", "title": "Oil change"}, '
            '"summary": "Log an oil change"}'
        )

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

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
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
    assert calls["decision"] == 1

    final2 = None
    async for update in graph.astream(Command(resume="confirm"), config, stream_mode="values"):
        final2 = update

    assert "__interrupt__" not in final2
    assert final2["action_result"] == {"status": "done", "summary": "Log an oil change"}
    assert calls["decision"] == 1


async def test_graph_action_flow_reject_records_cancellation_without_writing(monkeypatch):
    async def fake_complete(messages):
        if "routing classifier" in messages[0]["content"]:
            return "log_or_schedule"
        return (
            '{"tool": "create_log", "args": {"entity_id": "e1", "type": "service", '
            '"occurred_at": "2026-07-18", "title": "Oil change"}, '
            '"summary": "Log an oil change"}'
        )

    async def fake_list_entities(cookie):
        return [_VEHICLE_ENTITY]

    async def failing_create_log(cookie, args):
        raise AssertionError("must not write when the user rejects the proposal")

    async def fake_gather(query, cookie, entities=None, matched=None):
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
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

    assert final["action_result"] == {"status": "cancelled", "summary": "Log an oil change"}


# --- graph-level integration (MemorySaver, not Redis) -----------------------


async def _build_test_graph():
    return build_graph_builder().compile(checkpointer=MemorySaver())


async def test_graph_routes_vehicle_query_to_vehicle_specialist(monkeypatch):
    async def fake_complete(messages):
        return "vehicle"

    async def fake_gather(query, cookie):
        return [_ENTITY]

    async def fake_retrieve(query):
        return [_CHUNK]

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
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
    async def fake_complete(messages):
        # First call is the supervisor's classification, second is the
        # research node's tool-decision — distinguish by prompt content.
        if "routing classifier" in messages[0]["content"]:
            return "research"
        return '{"tool": null}'

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": None, "thread_id": "t2"}}
    result = await graph.ainvoke({"query": "what does the manual say"}, config)

    assert result["selected_agent"] == "research"
    assert result["persona"] == RESEARCH_PERSONA


async def test_graph_falls_back_to_general_on_unroutable_query(monkeypatch):
    async def fake_complete(messages):
        return "not a real category"

    async def fake_gather(query, cookie):
        return []

    async def fake_retrieve(query):
        return []

    monkeypatch.setattr(nodes_module.ollama, "complete", fake_complete)
    monkeypatch.setattr(nodes_module.entity_grounding, "gather_entity_context", fake_gather)
    monkeypatch.setattr(nodes_module.retrieval, "retrieve_context", fake_retrieve)

    graph = await _build_test_graph()
    config = {"configurable": {"cookie": None, "thread_id": "t3"}}
    result = await graph.ainvoke({"query": "tell me a joke"}, config)

    assert result["selected_agent"] == "general"
    assert result["persona"] == GENERAL_PERSONA
