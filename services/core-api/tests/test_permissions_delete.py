from tests.conftest import set_session_role

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create_entity(client, payload=VEHICLE_PAYLOAD) -> str:
    resp = client.post("/entities", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_member_403_on_entity_delete_owner_succeeds(client):
    entity_id = _create_entity(client)

    set_session_role("member")
    assert client.delete(f"/entities/{entity_id}").status_code == 403

    set_session_role("owner")
    assert client.delete(f"/entities/{entity_id}").status_code == 204


def test_member_403_on_log_delete_owner_succeeds(client):
    entity_id = _create_entity(client)
    log_id = client.post(
        "/logs",
        json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "Note"},
    ).json()["id"]

    set_session_role("member")
    assert client.delete(f"/logs/{log_id}").status_code == 403

    set_session_role("owner")
    assert client.delete(f"/logs/{log_id}").status_code == 204


def test_member_403_on_schedule_delete_owner_succeeds(client):
    entity_id = _create_entity(client)
    schedule_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 90,
            "starting_at": "2026-01-01",
        },
    ).json()["id"]

    set_session_role("member")
    assert client.delete(f"/schedules/{schedule_id}").status_code == 403

    set_session_role("owner")
    assert client.delete(f"/schedules/{schedule_id}").status_code == 204


def test_member_403_on_document_delete_owner_succeeds(client):
    entity_id = _create_entity(client)
    document_id = client.post(
        "/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"document_type": "manual", "entity_ids": [entity_id]},
    ).json()["id"]

    set_session_role("member")
    assert client.delete(f"/documents/{document_id}").status_code == 403

    set_session_role("owner")
    assert client.delete(f"/documents/{document_id}").status_code == 204
