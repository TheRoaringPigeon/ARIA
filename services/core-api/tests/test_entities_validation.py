"""Length caps on EntityCreate/EntityUpdate free-form fields
(scaling-debt.md #7) — these bound the worst-case per-document cost of
`_search_filter`'s regex/$objectToArray scan, not domain-shape rules, so
they're exercised here rather than alongside the domain-specific
attribute validation tests.
"""

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def test_create_entity_rejects_oversized_name(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "x" * 201})
    assert resp.status_code == 422


def test_create_entity_accepts_name_at_max_length(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "x" * 200})
    assert resp.status_code == 201


def test_create_entity_rejects_oversized_location(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "location": "x" * 201})
    assert resp.status_code == 422


def test_create_entity_rejects_oversized_tag(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["x" * 101]})
    assert resp.status_code == 422


def test_create_entity_rejects_too_many_tags(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": [f"tag{n}" for n in range(51)]})
    assert resp.status_code == 422


def test_create_entity_accepts_tags_at_max_count(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": [f"tag{n}" for n in range(50)]})
    assert resp.status_code == 201


def test_create_entity_rejects_oversized_specs_key(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "specs": {"k" * 201: "v"}})
    assert resp.status_code == 422


def test_create_entity_rejects_oversized_specs_value(client):
    resp = client.post("/entities", json={**VEHICLE_PAYLOAD, "specs": {"color": "v" * 201}})
    assert resp.status_code == 422


def test_create_entity_rejects_too_many_specs(client):
    resp = client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "specs": {f"k{n}": "v" for n in range(51)}}
    )
    assert resp.status_code == 422


def test_update_entity_rejects_oversized_name(client):
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    resp = client.patch(f"/entities/{entity_id}", json={"name": "x" * 201})
    assert resp.status_code == 422


def test_update_entity_rejects_oversized_tag(client):
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    resp = client.patch(f"/entities/{entity_id}", json={"tags": ["x" * 101]})
    assert resp.status_code == 422


def test_update_entity_rejects_oversized_specs_value(client):
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    resp = client.patch(f"/entities/{entity_id}", json={"specs": {"color": "v" * 201}})
    assert resp.status_code == 422
