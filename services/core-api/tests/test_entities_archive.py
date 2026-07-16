VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def test_archived_entity_excluded_from_default_list(client):
    create_resp = client.post("/entities", json=VEHICLE_PAYLOAD)
    assert create_resp.status_code == 201
    entity_id = create_resp.json()["id"]

    list_resp = client.get("/entities")
    assert any(e["id"] == entity_id for e in list_resp.json())

    archive_resp = client.post(f"/entities/{entity_id}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["archived_at"] is not None

    list_after = client.get("/entities")
    assert all(e["id"] != entity_id for e in list_after.json())

    list_with_archived = client.get("/entities", params={"include_archived": "true"})
    assert any(e["id"] == entity_id for e in list_with_archived.json())


def test_restore_brings_entity_back(client):
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    client.post(f"/entities/{entity_id}/archive")

    restore_resp = client.post(f"/entities/{entity_id}/restore")
    assert restore_resp.status_code == 200
    assert restore_resp.json()["archived_at"] is None

    list_resp = client.get("/entities")
    assert any(e["id"] == entity_id for e in list_resp.json())


def test_mismatched_domain_and_attributes_rejected(client):
    # attributes={"domain": "equipment"} parses fine as EquipmentAttrs on its
    # own (all its fields are optional) — the mismatch against the entity's
    # top-level domain="vehicle" is only caught by EntityBase's own
    # model_validator once the router constructs the full entity, which
    # surfaces as a 400 (see routers/entities.py's ValidationError handling),
    # not a 422 from request-body parsing.
    bad_payload = {
        "domain": "vehicle",
        "name": "Bad Entity",
        "status": "active",
        "attributes": {"domain": "equipment"},
    }
    resp = client.post("/entities", json=bad_payload)
    assert resp.status_code == 400
