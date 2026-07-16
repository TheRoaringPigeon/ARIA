VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create_entity(client, payload) -> str:
    resp = client.post("/entities", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_delete_entity_removes_it(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    get_resp = client.get(f"/entities/{entity_id}")
    assert get_resp.status_code == 404


async def test_delete_entity_cascades_logs_and_schedules(client, mock_db):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    log_id = client.post(
        "/logs",
        json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "Temp"},
    ).json()["id"]

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

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    assert await mock_db.logs.find_one({"_id": log_id}) is None
    assert await mock_db.schedules.find_one({"_id": schedule_id}) is None


def test_delete_nonexistent_entity_404(client):
    resp = client.delete("/entities/does-not-exist")
    assert resp.status_code == 404
