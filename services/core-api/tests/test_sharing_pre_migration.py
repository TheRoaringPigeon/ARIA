"""Regression coverage for a real bug caught live, not by this suite: an
entity/document created before `shared_with` existed has no such key in its
stored Mongo document at all (only the Pydantic model's default applies, and
only when actually validated through it) — every direct `doc["shared_with"]`
access in the sharing-check code paths raised `KeyError` against a genuinely
pre-existing household migrated onto M9. Every test here strips the key
from a real document after creating it via the normal API, simulating
exactly that pre-migration shape, then exercises every endpoint that reads
sharing off the raw dict.
"""

from tests.conftest import set_session_role

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


async def _create_entity_without_shared_with(client, mock_db) -> str:
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    await mock_db.entities.update_one({"_id": entity_id}, {"$unset": {"shared_with": ""}})
    return entity_id


async def test_get_entity_defaults_to_household_when_field_missing(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    resp = client.get(f"/entities/{entity_id}")

    assert resp.status_code == 200
    assert resp.json()["shared_with"] == "household"


async def test_list_entities_as_member_still_includes_it(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    set_session_role("member")
    resp = client.get("/entities")

    assert resp.status_code == 200
    assert entity_id in {e["id"] for e in resp.json()}


async def test_update_archive_delete_all_work_without_the_field(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    assert client.patch(f"/entities/{entity_id}", json={"name": "Renamed"}).status_code == 200
    assert client.post(f"/entities/{entity_id}/archive").status_code == 200
    assert client.post(f"/entities/{entity_id}/restore").status_code == 200
    assert client.delete(f"/entities/{entity_id}").status_code == 204


async def test_create_log_against_it_works_without_the_field(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    resp = client.post(
        "/logs",
        json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "ok"},
    )

    assert resp.status_code == 201


async def test_list_entity_logs_and_schedules_and_documents_work_without_the_field(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    assert client.get(f"/entities/{entity_id}/logs").status_code == 200
    assert client.get(f"/entities/{entity_id}/schedules").status_code == 200
    assert client.get(f"/entities/{entity_id}/documents").status_code == 200


async def test_document_endpoints_work_without_the_field(client, mock_db):
    entity_id = await _create_entity_without_shared_with(client, mock_db)
    document_id = client.post(
        "/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"document_type": "manual", "entity_ids": [entity_id]},
    ).json()["id"]
    await mock_db.documents.update_one({"_id": document_id}, {"$unset": {"shared_with": ""}})

    assert client.get(f"/documents/{document_id}").status_code == 200
    assert client.get(f"/documents/{document_id}/file").status_code == 200
    assert document_id in {
        d["id"] for d in client.get(f"/entities/{entity_id}/documents").json()
    }
    assert client.delete(f"/documents/{document_id}").status_code == 204


async def test_member_without_shared_with_field_still_gets_household_default(client, mock_db):
    """Not just the owner path (who always has access regardless) — a
    *member* reading a pre-migration entity must also default to
    "household" access, not 404 because the field happens to be absent
    rather than literally equal to `"household"`.
    """
    entity_id = await _create_entity_without_shared_with(client, mock_db)

    set_session_role("member")
    resp = client.get(f"/entities/{entity_id}")

    assert resp.status_code == 200
    assert resp.json()["shared_with"] == "household"
