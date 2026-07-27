from datetime import date, timedelta

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

# All schedule/occurrence dates below are anchored to date.today() + an offset
# rather than hardcoded calendar dates — schedule creation stamps created_at
# as "now", and project_occurrences floors every occurrence at created_at, so
# a fixed past/near date would get clipped depending on which real day the
# suite happens to run on (see project_occurrences' created_at-floor
# behavior in app/logic/schedules.py).
FUTURE = date.today() + timedelta(days=90)


def _iso(d: date) -> str:
    return d.isoformat()


def _create_entity(client, payload) -> str:
    resp = client.post("/entities", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_calendar_rejects_backwards_range(client):
    resp = client.get("/schedules/calendar", params={"from": _iso(FUTURE), "to": _iso(FUTURE - timedelta(days=1))})
    assert resp.status_code == 422


def test_calendar_rejects_oversized_range(client):
    resp = client.get(
        "/schedules/calendar", params={"from": "2020-01-01", "to": "2026-01-01"}
    )
    assert resp.status_code == 422


def test_calendar_projects_time_based_schedule_within_range(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Check tire pressure",
            "interval_type": "time",
            "interval_days": 7,
            "starting_at": _iso(FUTURE),
        },
    )

    resp = client.get(
        "/schedules/calendar",
        params={"from": _iso(FUTURE), "to": _iso(FUTURE + timedelta(days=28))},
    )
    assert resp.status_code == 200
    occurrences = resp.json()
    dates = [o["occurrence_date"] for o in occurrences]
    assert dates == [
        _iso(FUTURE + timedelta(days=7)),
        _iso(FUTURE + timedelta(days=14)),
        _iso(FUTURE + timedelta(days=21)),
        _iso(FUTURE + timedelta(days=28)),
    ]
    assert all(o["entity_name"] == VEHICLE_PAYLOAD["name"] for o in occurrences)


def test_calendar_shows_completed_once_schedule_on_its_day(client):
    entity_id = _create_entity(client, PERSON_PAYLOAD)
    planned = _iso(FUTURE)
    schedule_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": planned,
        },
    ).json()["id"]

    client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "call",
            "occurred_at": planned,
            "title": "Had coffee",
            "schedule_id": schedule_id,
        },
    )

    resp = client.get(
        "/schedules/calendar",
        params={"from": _iso(FUTURE - timedelta(days=10)), "to": _iso(FUTURE + timedelta(days=10))},
    )
    occurrences = resp.json()
    assert len(occurrences) == 1
    assert occurrences[0]["occurrence_date"] == planned
    assert occurrences[0]["is_next_due"] is False  # next_due_at cleared on completion


def test_calendar_filters_by_domain(client):
    vehicle_id = _create_entity(client, VEHICLE_PAYLOAD)
    person_id = _create_entity(client, PERSON_PAYLOAD)
    client.post(
        "/schedules",
        json={
            "entity_id": vehicle_id,
            "title": "Oil change",
            "interval_type": "once",
            "planned_at": _iso(FUTURE),
        },
    )
    client.post(
        "/schedules",
        json={
            "entity_id": person_id,
            "title": "Coffee with Sandra",
            "interval_type": "once",
            "planned_at": _iso(FUTURE + timedelta(days=1)),
        },
    )

    resp = client.get(
        "/schedules/calendar",
        params={
            "from": _iso(FUTURE - timedelta(days=5)),
            "to": _iso(FUTURE + timedelta(days=5)),
            "domain": "vehicle",
        },
    )
    occurrences = resp.json()
    assert len(occurrences) == 1
    assert occurrences[0]["title"] == "Oil change"


def test_calendar_excludes_occurrences_before_schedule_created(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Wash car",
            "interval_type": "monthly",
            "monthly_day": 1,
            "starting_at": "2020-01-01",
        },
    )

    # created_at is "today" (test run time), so a range far in the past
    # (well before creation) must come back empty rather than projecting
    # phantom historical occurrences.
    resp = client.get("/schedules/calendar", params={"from": "2000-01-01", "to": "2000-01-31"})
    assert resp.status_code == 200
    assert resp.json() == []
