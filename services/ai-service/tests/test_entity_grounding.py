import httpx

import app.core_api_client as core_api_client_module
from app.config import settings
from app.entity_grounding import _find_matching_entities, _word_boundary_match, gather_entity_context

ALLEN = {
    "id": "e1",
    "domain": "person",
    "name": "Allen Woodward",
    "tags": ["Dad"],
    "specs": {},
    "attributes": {"relationship": "father", "birthday": "1958-03-12"},
}

CIVIC = {
    "id": "e2",
    "domain": "vehicle",
    "name": "Civic",
    "tags": ["daily driver"],
    "specs": {"paint_code": "NH-731P"},
    "attributes": {},
}


def _make_401_error():
    request = httpx.Request("GET", "http://core-api:8000/entities")
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError("unauthorized", request=request, response=response)


def test_word_boundary_match_matches_whole_word():
    assert _word_boundary_match("Dad", "give me talking points with my dad")


def test_word_boundary_match_rejects_substring_inside_longer_word():
    assert not _word_boundary_match("Dad", "Daddy-long-legs are spiders")


def test_find_matching_entities_matches_on_name_or_tag():
    matched = _find_matching_entities("what does the civic need?", [ALLEN, CIVIC])
    assert matched == [CIVIC]


def test_find_matching_entities_returns_empty_when_nothing_matches():
    assert _find_matching_entities("what's the weather like", [ALLEN, CIVIC]) == []


def test_find_matching_entities_caps_at_entity_match_limit(monkeypatch):
    monkeypatch.setattr(settings, "entity_match_limit", 1)
    entities = [
        {**ALLEN, "id": "e1", "name": "Dad"},
        {**CIVIC, "id": "e2", "name": "Dad2", "tags": ["Dad"]},
    ]
    matched = _find_matching_entities("dad", entities)
    assert len(matched) == 1


async def test_gather_entity_context_returns_empty_with_no_cookie(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("should not fetch entities without a cookie")

    monkeypatch.setattr(core_api_client_module, "list_entities", fail_if_called)

    assert await gather_entity_context("give me talking points with dad", None) == []


async def test_gather_entity_context_degrades_on_expired_session(monkeypatch):
    async def raise_401(cookie):
        raise _make_401_error()

    monkeypatch.setattr(core_api_client_module, "list_entities", raise_401)

    assert await gather_entity_context("dad", "stale-cookie") == []


async def test_gather_entity_context_degrades_on_core_api_unreachable(monkeypatch):
    async def raise_connect_error(cookie):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(core_api_client_module, "list_entities", raise_connect_error)

    assert await gather_entity_context("dad", "a-cookie") == []


async def test_gather_entity_context_returns_empty_when_nothing_matches(monkeypatch):
    async def fake_list_entities(cookie):
        return [ALLEN, CIVIC]

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)

    assert await gather_entity_context("what's the weather like", "a-cookie") == []


async def test_gather_entity_context_returns_context_for_matched_entity(monkeypatch):
    async def fake_list_entities(cookie):
        return [ALLEN, CIVIC]

    async def fake_list_entity_logs(cookie, entity_id):
        return [
            {
                "occurred_at": "2026-05-01",
                "type": "conversation",
                "title": "Talked on the phone",
                "description": "Mentioned he's close to finishing his book.",
            }
        ]

    async def fake_list_entity_schedules(cookie, entity_id):
        return [{"title": "Call Dad monthly", "next_due_at": "2026-08-01"}]

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )

    result = await gather_entity_context("give me talking points with dad", "a-cookie")

    assert len(result) == 1
    context = result[0]
    assert context.name == "Allen Woodward"
    assert context.domain == "person"
    assert context.tags == ["Dad"]
    assert context.person_attrs == {"Relationship": "father", "Birthday": "1958-03-12"}
    assert len(context.logs) == 1
    assert "book" in context.logs[0]["description"]
    assert context.schedules[0]["title"] == "Call Dad monthly"


async def test_gather_entity_context_isolates_one_entity_failure(monkeypatch):
    async def fake_list_entities(cookie):
        return [ALLEN, CIVIC]

    async def fake_list_entity_logs(cookie, entity_id):
        if entity_id == CIVIC["id"]:
            raise httpx.ConnectError("connection refused")
        return []

    async def fake_list_entity_schedules(cookie, entity_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )

    result = await gather_entity_context("talking points with dad about the civic", "a-cookie")

    assert len(result) == 1
    assert result[0].name == "Allen Woodward"


async def test_gather_entity_context_caps_logs_at_entity_logs_limit(monkeypatch):
    monkeypatch.setattr(settings, "entity_logs_limit", 2)

    async def fake_list_entities(cookie):
        return [ALLEN]

    async def fake_list_entity_logs(cookie, entity_id):
        return [{"occurred_at": f"2026-0{i}-01", "type": "note", "title": f"log {i}"} for i in range(1, 6)]

    async def fake_list_entity_schedules(cookie, entity_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )

    result = await gather_entity_context("dad", "a-cookie")

    assert len(result[0].logs) == 2


async def test_gather_entity_context_only_builds_person_attrs_for_person_domain(monkeypatch):
    async def fake_list_entities(cookie):
        return [CIVIC]

    async def fake_list_entity_logs(cookie, entity_id):
        return []

    async def fake_list_entity_schedules(cookie, entity_id):
        return []

    monkeypatch.setattr(core_api_client_module, "list_entities", fake_list_entities)
    monkeypatch.setattr(core_api_client_module, "list_entity_logs", fake_list_entity_logs)
    monkeypatch.setattr(
        core_api_client_module, "list_entity_schedules", fake_list_entity_schedules
    )

    result = await gather_entity_context("what does the civic need", "a-cookie")

    assert result[0].person_attrs is None
    assert result[0].specs == {"paint_code": "NH-731P"}
