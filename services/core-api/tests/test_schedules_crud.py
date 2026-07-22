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


def _create_entity(client, payload) -> str:
    resp = client.post("/entities", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_update_schedule_title_and_interval(client):
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


def test_update_schedule_moves_anchor_date(client):
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

    resp = client.patch(f"/schedules/{schedule_id}", json={"starting_at": "2026-02-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_completed_at"] == "2026-02-01"
    assert body["next_due_at"] == "2026-05-02"  # 2026-02-01 + 90 days


def test_update_schedule_omitted_starting_at_preserves_completion(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    schedule = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 90,
            "starting_at": "2026-01-01",
        },
    ).json()

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Oil change",
            "schedule_id": schedule["id"],
        },
    ).json()["id"]

    # A real completion has moved last_completed_at to 2026-03-01. Editing
    # just the title (starting_at omitted, not resent) must not touch it —
    # this is the case the frontend relies on for "save without touching the
    # date field": it leaves starting_at out of the request entirely rather
    # than resending whatever the form loaded with.
    resp = client.patch(f"/schedules/{schedule['id']}", json={"title": "Oil + filter change"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Oil + filter change"
    assert body["last_completed_at"] == "2026-03-01"
    assert body["last_completed_log_id"] == log_id
    assert body["next_due_at"] == "2026-05-30"  # unchanged: 2026-03-01 + 90 days


def test_update_schedule_resending_current_anchor_date_is_a_noop(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    schedule = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 90,
            "starting_at": "2026-01-01",
        },
    ).json()

    log_id = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Oil change",
            "schedule_id": schedule["id"],
        },
    ).json()["id"]

    # Resending starting_at equal to the *current* (post-completion)
    # last_completed_at — e.g. a freshly-loaded edit form the user didn't
    # touch the date field on — is a no-op, not a reseed.
    resp = client.patch(
        f"/schedules/{schedule['id']}",
        json={"title": "Oil + filter change", "starting_at": "2026-03-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_completed_at"] == "2026-03-01"
    assert body["last_completed_log_id"] == log_id
    assert body["next_due_at"] == "2026-05-30"


def test_update_schedule_switches_interval_type(client):
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

    resp = client.patch(
        f"/schedules/{schedule_id}",
        json={"interval_type": "monthly", "monthly_day": 15, "starting_at": "2026-01-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["interval_type"] == "monthly"
    assert body["monthly_day"] == 15
    # Stale "time" fields must not linger once the type has switched.
    assert body["interval_days"] is None
    assert body["next_due_at"] == "2026-01-15"  # next 15th strictly after 2026-01-01


def test_update_schedule_switch_type_requires_new_types_fields(client):
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

    resp = client.patch(f"/schedules/{schedule_id}", json={"interval_type": "monthly"})
    assert resp.status_code == 400


def test_update_nonexistent_schedule_404(client):
    resp = client.patch("/schedules/does-not-exist", json={"title": "x"})
    assert resp.status_code == 404


def test_delete_schedule_removes_it(client):
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
    resp = client.delete("/schedules/does-not-exist")
    assert resp.status_code == 404


def test_deleting_schedule_leaves_linked_log_intact(client):
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


def test_due_soon_domain_filter(client):
    vehicle_id = _create_entity(client, VEHICLE_PAYLOAD)
    person_id = _create_entity(client, PERSON_PAYLOAD)

    oil_change_id = client.post(
        "/schedules",
        json={
            "entity_id": vehicle_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 90,
            "starting_at": "2026-01-01",
        },
    ).json()["id"]

    coffee_id = client.post(
        "/schedules",
        json={
            "entity_id": person_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    vehicle_due = client.get("/schedules/due-soon", params={"within_days": 365, "domain": "vehicle"}).json()
    assert [item["schedule"]["id"] for item in vehicle_due] == [oil_change_id]

    person_due = client.get("/schedules/due-soon", params={"within_days": 365, "domain": "person"}).json()
    assert [item["schedule"]["id"] for item in person_due] == [coffee_id]

    all_due = client.get("/schedules/due-soon", params={"within_days": 365}).json()
    assert {item["schedule"]["id"] for item in all_due} == {oil_change_id, coffee_id}
