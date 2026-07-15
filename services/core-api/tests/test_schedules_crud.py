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


def test_update_schedule_title_and_interval(client):
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

    resp = client.patch(f"/schedules/{schedule_id}", json={"title": "Oil + filter change", "interval_days": 180})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Oil + filter change"
    assert body["interval_days"] == 180
    assert body["next_due_at"] == "2026-06-30"  # 2026-01-01 + 180 days


def test_update_once_plan_reschedules_date(client):
    _login(client)
    entity_id = _create_entity(client, PERSON_PAYLOAD)

    plan_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    resp = client.patch(f"/schedules/{plan_id}", json={"planned_at": "2026-07-27"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["planned_at"] == "2026-07-27"
    assert body["next_due_at"] == "2026-07-27"


def test_update_nonexistent_schedule_404(client):
    _login(client)
    resp = client.patch("/schedules/does-not-exist", json={"title": "x"})
    assert resp.status_code == 404


def test_delete_schedule_removes_it(client):
    _login(client)
    entity_id = _create_entity(client, PERSON_PAYLOAD)

    plan_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    resp = client.delete(f"/schedules/{plan_id}")
    assert resp.status_code == 204

    remaining = client.get(f"/entities/{entity_id}/schedules").json()
    assert all(s["id"] != plan_id for s in remaining)

    due = client.get("/schedules/due-soon", params={"within_days": 365}).json()
    assert all(item["schedule"]["id"] != plan_id for item in due)


def test_delete_nonexistent_schedule_404(client):
    _login(client)
    resp = client.delete("/schedules/does-not-exist")
    assert resp.status_code == 404


def test_deleting_schedule_leaves_linked_log_intact(client):
    _login(client)
    entity_id = _create_entity(client, PERSON_PAYLOAD)

    plan_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "meeting",
            "occurred_at": "2026-07-20",
            "title": "Coffee with Sandra",
            "schedule_id": plan_id,
        },
    ).json()["id"]

    resp = client.delete(f"/schedules/{plan_id}")
    assert resp.status_code == 204

    logs = client.get(f"/entities/{entity_id}/logs").json()
    assert any(log["id"] == log_id and log["schedule_id"] == plan_id for log in logs)
