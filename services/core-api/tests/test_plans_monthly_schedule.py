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


def test_monthly_day_of_month_seeds_next_due(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Pay rent",
            "interval_type": "monthly",
            "monthly_day": 4,
            "starting_at": "2026-07-15",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["next_due_at"] == "2026-08-04"


def test_monthly_nth_weekday_seeds_next_due(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Book club",
            "interval_type": "monthly",
            "monthly_weekday": 4,
            "monthly_week_index": 2,
            "starting_at": "2023-12-15",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["next_due_at"] == "2024-01-12"


def test_monthly_rejects_day_and_weekday_together(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Bad rule",
            "interval_type": "monthly",
            "monthly_day": 4,
            "monthly_weekday": 4,
            "monthly_week_index": 2,
        },
    )
    assert resp.status_code == 422


def test_monthly_rejects_neither_day_nor_weekday(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={"entity_id": entity_id, "title": "Bad rule", "interval_type": "monthly"},
    )
    assert resp.status_code == 422


def test_monthly_rejects_invalid_day(client):
    entity_id = _create_person(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Bad rule",
            "interval_type": "monthly",
            "monthly_day": 32,
        },
    )
    # Range check lives on ScheduleCreate, same as planned_at's required-ness
    # for "once" — 422 from request parsing, not a handler-level 400.
    assert resp.status_code == 422


def test_completing_monthly_plan_advances_next_due(client):
    entity_id = _create_person(client)

    schedule_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Call Mom",
            "interval_type": "monthly",
            "monthly_day": 4,
            "starting_at": "2026-07-15",
        },
    ).json()["id"]

    log_resp = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "call",
            "occurred_at": "2026-08-04",
            "title": "Called Mom",
            "schedule_id": schedule_id,
        },
    )
    assert log_resp.status_code == 201

    schedule = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert schedule["last_completed_at"] == "2026-08-04"
    assert schedule["next_due_at"] == "2026-09-04"


def test_monthly_shows_up_in_due_soon(client):
    entity_id = _create_person(client)

    client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Pay rent",
            "interval_type": "monthly",
            "monthly_day": 1,
            "starting_at": "2020-01-01",
        },
    )

    due = client.get("/schedules/due-soon", params={"within_days": 30}).json()
    assert len(due) == 1
    assert due[0]["entity_name"] == PERSON_PAYLOAD["name"]
