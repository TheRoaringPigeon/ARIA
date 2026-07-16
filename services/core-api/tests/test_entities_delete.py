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


def _upload(client, entity_ids, *, filename="manual.pdf"):
    data = {"document_type": "manual", "entity_ids": entity_ids}
    resp = client.post(
        "/documents",
        files={"file": (filename, b"%PDF-1.4 fake", "application/pdf")},
        data=data,
    )
    assert resp.status_code == 201
    return resp.json()


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


async def test_delete_entity_enqueues_cleanup_for_sole_referencing_document(
    client, mock_db, celery_calls
):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    document = _upload(client, [entity_id])

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    assert ("app.tasks.delete_document.delete_document", [document["id"], document["storage_path"]]) in celery_calls
    # Cleanup is deferred to the worker task, not done inline — the Mongo
    # row itself is untouched by the entity-delete request.
    assert await mock_db.documents.find_one({"_id": document["id"]}) is not None


async def test_delete_entity_only_unlinks_document_shared_with_another_entity(
    client, mock_db, celery_calls
):
    entity_a = _create_entity(client, VEHICLE_PAYLOAD)
    entity_b = _create_entity(client, VEHICLE_PAYLOAD)
    document = _upload(client, [entity_a, entity_b])

    resp = client.delete(f"/entities/{entity_a}")
    assert resp.status_code == 204

    assert not any(call[0] == "app.tasks.delete_document.delete_document" for call in celery_calls)
    stored = await mock_db.documents.find_one({"_id": document["id"]})
    assert stored["entity_ids"] == [entity_b]
