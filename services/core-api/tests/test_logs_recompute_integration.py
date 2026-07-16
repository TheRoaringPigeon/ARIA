VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Oil Change Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create_vehicle(client) -> str:
    resp = client.post("/entities", json=VEHICLE_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_schedule_seeds_next_due_at_on_creation(client):
    entity_id = _create_vehicle(client)

    resp = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 90,
            "starting_at": "2026-01-01",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["next_due_at"] == "2026-04-01"


def test_log_with_schedule_id_recomputes_next_due(client):
    entity_id = _create_vehicle(client)

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

    log_resp = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Oil change",
            "schedule_id": schedule["id"],
        },
    )
    assert log_resp.status_code == 201

    updated = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert updated["last_completed_at"] == "2026-03-01"
    assert updated["next_due_at"] == "2026-05-30"  # 2026-03-01 + 90 days
    assert updated["last_completed_log_id"] == log_resp.json()["id"]


def test_logging_without_schedule_id_is_valid(client):
    entity_id = _create_vehicle(client)

    resp = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "note",
            "occurred_at": "2026-03-01",
            "title": "Just a note",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["schedule_id"] is None


def test_usage_based_log_requires_matching_metric(client):
    entity_id = _create_vehicle(client)

    schedule_id = client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Tire rotation",
            "interval_type": "usage",
            "usage_metric": "odometer_reading",
            "interval_usage_amount": 5000,
            "starting_usage_value": 10000,
        },
    ).json()["id"]

    missing_metric = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Tire rotation",
            "schedule_id": schedule_id,
        },
    )
    assert missing_metric.status_code == 400

    ok = client.post(
        "/logs",
        json={
            "entity_id": entity_id,
            "type": "service",
            "occurred_at": "2026-03-01",
            "title": "Tire rotation",
            "schedule_id": schedule_id,
            "metrics": {"odometer_reading": "15000"},
        },
    )
    assert ok.status_code == 201

    updated = client.get(f"/entities/{entity_id}/schedules").json()[0]
    assert updated["last_completed_usage_value"] == 15000
    assert updated["next_due_usage_value"] == 20000


def test_due_soon_reflects_time_based_schedule(client):
    entity_id = _create_vehicle(client)

    client.post(
        "/schedules",
        json={
            "entity_id": entity_id,
            "title": "Oil change",
            "interval_type": "time",
            "interval_days": 1,
            "starting_at": "2020-01-01",
        },
    )

    # Already overdue (due 2020-01-02), so it satisfies next_due_at <= horizon
    # regardless of window size — no need to push within_days near its cap.
    due = client.get("/schedules/due-soon", params={"within_days": 30}).json()
    assert len(due) == 1
    assert due[0]["entity_name"] == VEHICLE_PAYLOAD["name"]
    assert due[0]["is_overdue"] is True
