from langgraph.checkpoint.memory import MemorySaver

import app.agents.nodes as nodes_module
from app.agents.graph import build_graph_builder
from app.agents.nodes import (
    general_node,
    maintenance_node,
    research_node,
    supervisor_node,
    vehicle_node,
)
from app.agents.state import GENERAL_PERSONA, MAINTENANCE_PERSONA, RESEARCH_PERSONA, VEHICLE_PERSONA
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
