PERSON_PAYLOAD = {
    "domain": "person",
    "name": "Sandra Lee",
    "status": "active",
    "attributes": {"domain": "person"},
}


def _create_person(client) -> str:
    resp = client.post("/entities", json=PERSON_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_once_schedule_seeds_next_due_at_planned_date(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["next_due_at"] == "2026-07-20"
    assert body["last_completed_at"] is None


def test_once_schedule_accepts_planned_time(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Dinner with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
            "planned_time": "19:00",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["planned_time"] == "19:00"


def test_planned_time_rejects_bad_format(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Dinner with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
            "planned_time": "7pm",
        },
    )
    # Unlike planned_at's required-ness (checked on ScheduleCreate, so a
    # missing value 422s during request parsing), the format check lives on
    # the canonical Schedule model, constructed inside the router's
    # try/except ValidationError -> 400 block.
    assert resp.status_code == 400


def test_update_schedule_sets_planned_time(client):
    entity_id = _create_person(client)

    plan_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    resp = client.patch(f"/schedules/{plan_id}", json={"planned_time": "09:30"})
    assert resp.status_code == 200
    assert resp.json()["planned_time"] == "09:30"


def test_once_schedule_requires_planned_at(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={"entity_id": entity_id, "title": "Coffee with Sandra", "interval_type": "once"},
    )
    # ScheduleCreate's cross-field validator raises during FastAPI's request-body
    # parsing (before the route handler runs), so this is a 422, not the 400 the
    # handler itself would raise for entity/schedule lookup failures.
    assert resp.status_code == 422


def test_completing_once_schedule_clears_next_due(client):
    entity_id = _create_person(client)

    schedule_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2026-07-20",
        },
    ).json()["id"]

    log_resp = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "meeting",
            "occurred_at": "2026-07-20",
            "title": "Coffee with Sandra",
            "description": "Caught up, she started a new job at Acme.",
            "schedule_id": schedule_id,
        },
    )
    assert log_resp.status_code == 201

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_at"] == "2026-07-20"
    assert schedule["next_due_at"] is None  # done, nothing further due

    due = client.get("/schedules/due-soon", params={"within_days": 365}).json()
    assert all(item["schedule"]["id"] != schedule_id for item in due)


def test_deleting_completing_log_reverts_once_schedule_to_pending(client):
    entity_id = _create_person(client)

    schedule_id = client.post(
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
            "schedule_id": schedule_id,
        },
    ).json()["id"]

    resp = client.delete(f"/logs/{log_id}")
    assert resp.status_code == 204

    # Unlike time/usage schedules, "once" persists planned_at as a real
    # field rather than collapsing it into last_completed_at at creation —
    # so deleting the completing log can genuinely restore "still pending"
    # instead of resetting to an untracked None.
    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_at"] is None
    assert schedule["next_due_at"] == "2026-07-20"


def test_once_schedule_shows_up_in_due_soon(client):
    entity_id = _create_person(client)

    client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": "2020-01-02",
        },
    )

    due = client.get("/schedules/due-soon", params={"within_days": 30}).json()
    assert len(due) == 1
    assert due[0]["entity_name"] == PERSON_PAYLOAD["name"]
    assert due[0]["is_overdue"] is True
