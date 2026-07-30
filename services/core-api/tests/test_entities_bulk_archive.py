VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create(client, name="Test Truck"):
    payload = {**VEHICLE_PAYLOAD, "name": name}
    return client.post("/entities", json=payload).json()["id"]


def test_bulk_archive_then_bulk_restore(client):
    id1 = _create(client, "Truck 1")
    id2 = _create(client, "Truck 2")

    archive_resp = client.post("/entities/bulk-archive", json={"ids": [id1, id2]})
    assert archive_resp.status_code == 200
    body = archive_resp.json()
    assert sorted(body["succeeded"]) == sorted([id1, id2])
    assert body["not_found"] == []
    assert body["forbidden"] == []

    list_resp = client.get("/entities")
    assert all(e["id"] not in (id1, id2) for e in list_resp.json())

    restore_resp = client.post("/entities/bulk-restore", json={"ids": [id1, id2]})
    assert restore_resp.status_code == 200
    restore_body = restore_resp.json()
    assert sorted(restore_body["succeeded"]) == sorted([id1, id2])

    ids_after = {e["id"] for e in client.get("/entities").json()}
    assert {id1, id2} <= ids_after


def test_bulk_archive_unknown_id_is_not_found(client):
    resp = client.post("/entities/bulk-archive", json={"ids": ["does-not-exist"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == []
    assert body["not_found"] == ["does-not-exist"]
    assert body["forbidden"] == []


def test_bulk_archive_mixed_valid_and_invalid_ids(client):
    valid_id = _create(client, "Truck 3")

    resp = client.post("/entities/bulk-archive", json={"ids": [valid_id, "bogus-id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == [valid_id]
    assert body["not_found"] == ["bogus-id"]

    list_resp = client.get("/entities")
    assert all(e["id"] != valid_id for e in list_resp.json())


async def test_bulk_archive_entity_in_another_household_is_not_found(client, mock_db):
    doc = {
        "_id": "other-household-entity",
        "household_id": "other-household",
        "domain": "vehicle",
        "name": "Someone Else's Truck",
        "status": "active",
        "tags": [],
        "location": None,
        "specs": {},
        "attributes": {"domain": "vehicle"},
        "created_by": "someone-else",
        "archived_at": None,
    }
    await mock_db.entities.insert_one(doc)

    resp = client.post("/entities/bulk-archive", json={"ids": ["other-household-entity"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == []
    assert body["not_found"] == ["other-household-entity"]


def test_bulk_archive_empty_ids_rejected(client):
    resp = client.post("/entities/bulk-archive", json={"ids": []})
    assert resp.status_code == 422
