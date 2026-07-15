from app.config import settings

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}

PERSON_PAYLOAD = {
    "domain": "person",
    "name": "Sandra Lee",
    "status": "active",
    "attributes": {"domain": "person"},
}


def _login(client) -> None:
    resp = client.post("/auth/login", json={"password": settings.admin_password})
    assert resp.status_code == 200


def _create_entity(client, payload) -> str:
    resp = client.post("/entities", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_update_log_title_and_description(client):
    _login(client)
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "note",
            "occurred_at": "2026-03-01",
            "title": "Original title",
        },
    ).json()["id"]

    resp = client.patch(f"/logs/{log_id}", json={"title": "Updated title", "description": "more detail"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Updated title"
    assert body["description"] == "more detail"
    assert body["type"] == "note"  # untouched fields survive the merge


def test_update_log_rejects_type_invalid_for_domain(client):
    _login(client)
    entity_id = _create_entity(client, PERSON_PAYLOAD)

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "conversation",
            "occurred_at": "2026-03-01",
            "title": "Coffee",
        },
    ).json()["id"]

    resp = client.patch(f"/logs/{log_id}", json={"type": "service"})
    assert resp.status_code == 400


def test_update_nonexistent_log_404(client):
    _login(client)
    resp = client.patch("/logs/does-not-exist", json={"title": "x"})
    assert resp.status_code == 404


def test_delete_log_removes_it(client):
    _login(client)
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    log_id = client.post(
        "/logs",
        json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "Temp"},
    ).json()["id"]

    resp = client.delete(f"/logs/{log_id}")
    assert resp.status_code == 204

    remaining = client.get(f"/entities/{entity_id}/logs").json()
    assert all(log["id"] != log_id for log in remaining)


def test_delete_nonexistent_log_404(client):
    _login(client)
    resp = client.delete("/logs/does-not-exist")
    assert resp.status_code == 404


def test_editing_schedule_linked_log_recomputes_next_due(client):
    _login(client)
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

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

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Oil change",
            "schedule_id": schedule_id,
        },
    ).json()["id"]

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["next_due_at"] == "2026-05-30"  # 2026-03-01 + 90 days

    resp = client.patch(f"/logs/{log_id}", json={"occurred_at": "2026-03-15"})
    assert resp.status_code == 200

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_at"] == "2026-03-15"
    assert schedule["next_due_at"] == "2026-06-13"  # 2026-03-15 + 90 days


def test_deleting_schedule_linked_log_falls_back_to_previous_log(client):
    _login(client)
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

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

    first_log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-02-01",
            "title": "First oil change",
            "schedule_id": schedule_id,
        },
    ).json()["id"]

    second_log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Second oil change",
            "schedule_id": schedule_id,
        },
    ).json()["id"]

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_log_id"] == second_log_id
    assert schedule["last_completed_at"] == "2026-03-01"

    resp = client.delete(f"/logs/{second_log_id}")
    assert resp.status_code == 204

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_log_id"] == first_log_id
    assert schedule["last_completed_at"] == "2026-02-01"
    assert schedule["next_due_at"] == "2026-05-02"  # 2026-02-01 + 90 days


def test_deleting_only_schedule_linked_log_resets_schedule(client):
    _login(client)
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

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

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Oil change",
            "schedule_id": schedule_id,
        },
    ).json()["id"]

    resp = client.delete(f"/logs/{log_id}")
    assert resp.status_code == 204

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_log_id"] is None
    assert schedule["last_completed_at"] is None
    assert schedule["next_due_at"] is None
