from tests.conftest import set_session_role

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


async def test_delete_entity_hides_it_but_does_not_purge_it(client, mock_db):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    # Hidden from the normal API surface immediately...
    assert client.get(f"/entities/{entity_id}").status_code == 404
    assert entity_id not in {e["id"] for e in client.get("/entities").json()}

    # ...but not actually gone — that's purge_expired_trash's (worker) job
    # once the grace period lapses, not this route's.
    doc = await mock_db.entities.find_one({"_id": entity_id})
    assert doc is not None
    assert doc["pending_delete_at"] is not None


async def test_delete_entity_cascades_pending_delete_to_logs_and_schedules(client, mock_db):
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

    log_doc = await mock_db.logs.find_one({"_id": log_id})
    schedule_doc = await mock_db.schedules.find_one({"_id": schedule_id})
    assert log_doc is not None and log_doc["pending_delete_at"] is not None
    assert schedule_doc is not None and schedule_doc["pending_delete_at"] is not None

    # The entity itself 404s while trashed, so its sub-resources aren't
    # independently reachable either.
    assert client.get(f"/entities/{entity_id}/schedules").status_code == 404


def test_delete_nonexistent_entity_404(client):
    resp = client.delete("/entities/does-not-exist")
    assert resp.status_code == 404


async def test_delete_entity_defers_document_cleanup_until_purge(client, mock_db, celery_calls):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    document = _upload(client, [entity_id])

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    # Trashing doesn't touch documents at all — reversible state, same as
    # archive not unpinning. No cleanup enqueued, no unlink yet.
    assert not any(call[0] == "app.tasks.delete_document.delete_document" for call in celery_calls)
    stored = await mock_db.documents.find_one({"_id": document["id"]})
    assert stored["entity_ids"] == [entity_id]


async def test_delete_entity_leaves_pinned_entity_ids_untouched(client, mock_db):
    from datetime import datetime, timezone

    from tests.conftest import TEST_HOUSEHOLD_ID, TEST_USER_ID, TEST_USER_NAME

    await mock_db.users.insert_one(
        {
            "_id": TEST_USER_ID,
            "household_id": TEST_HOUSEHOLD_ID,
            "name": TEST_USER_NAME,
            "email": "test-user@example.com",
            "password_hash": "irrelevant",
            "role": "owner",
            "created_at": datetime.now(timezone.utc),
        }
    )

    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    pin_resp = client.patch("/users/me", json={"pinned_entity_ids": [entity_id]})
    assert pin_resp.status_code == 200

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204

    # Still pinned — trashing is reversible, same reasoning archive already
    # relies on ("archive does not unpin"). Only the purge sweep removes it.
    me = client.get("/users/me").json()
    assert entity_id in me["pinned_entity_ids"]


async def test_restore_entity_from_trash(client, mock_db):
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

    assert client.delete(f"/entities/{entity_id}").status_code == 204
    assert client.get(f"/entities/{entity_id}").status_code == 404

    resp = client.post(f"/entities/{entity_id}/restore-from-trash")
    assert resp.status_code == 200
    assert resp.json()["pending_delete_at"] is None

    assert client.get(f"/entities/{entity_id}").status_code == 200
    assert entity_id in {e["id"] for e in client.get("/entities").json()}

    schedule_doc = await mock_db.schedules.find_one({"_id": schedule_id})
    assert schedule_doc["pending_delete_at"] is None


def test_restore_from_trash_member_403_owner_succeeds(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    assert client.delete(f"/entities/{entity_id}").status_code == 204

    set_session_role("member")
    assert client.post(f"/entities/{entity_id}/restore-from-trash").status_code == 403

    set_session_role("owner")
    assert client.post(f"/entities/{entity_id}/restore-from-trash").status_code == 200


def test_list_trashed_entities_owner_only(client):
    kept_id = _create_entity(client, VEHICLE_PAYLOAD)
    trashed_id = _create_entity(client, VEHICLE_PAYLOAD)
    assert client.delete(f"/entities/{trashed_id}").status_code == 204

    set_session_role("member")
    assert client.get("/entities/trash").status_code == 403

    set_session_role("owner")
    resp = client.get("/entities/trash")
    assert resp.status_code == 200
    trashed_ids = {e["id"] for e in resp.json()}
    assert trashed_ids == {trashed_id}
    assert kept_id not in trashed_ids


def test_trashed_entity_rejects_update_archive_restore_and_redelete(client):
    entity_id = _create_entity(client, VEHICLE_PAYLOAD)
    assert client.delete(f"/entities/{entity_id}").status_code == 204

    # A trashed entity is hidden everywhere except the trash view and
    # restore-from-trash — a stale tab/client shouldn't still be able to
    # mutate it via any of the other single-entity routes.
    assert client.patch(f"/entities/{entity_id}", json={"name": "Renamed"}).status_code == 404
    assert client.post(f"/entities/{entity_id}/archive").status_code == 404
    assert client.post(f"/entities/{entity_id}/restore").status_code == 404
    assert client.delete(f"/entities/{entity_id}").status_code == 404


def test_bulk_archive_and_restore_treat_trashed_entity_as_not_found(client):
    kept_id = _create_entity(client, VEHICLE_PAYLOAD)
    trashed_id = _create_entity(client, VEHICLE_PAYLOAD)
    assert client.delete(f"/entities/{trashed_id}").status_code == 204

    archive_resp = client.post("/entities/bulk-archive", json={"ids": [kept_id, trashed_id]})
    assert archive_resp.status_code == 200
    archive_result = archive_resp.json()
    assert archive_result["succeeded"] == [kept_id]
    assert archive_result["not_found"] == [trashed_id]

    restore_resp = client.post("/entities/bulk-restore", json={"ids": [kept_id, trashed_id]})
    assert restore_resp.status_code == 200
    restore_result = restore_resp.json()
    assert restore_result["succeeded"] == [kept_id]
    assert restore_result["not_found"] == [trashed_id]
