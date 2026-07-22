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


def test_tags_are_distinct_and_sorted(client):
    client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["winter-ready", "diesel"]})
    client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "Other Truck", "tags": ["diesel"]})

    resp = client.get("/entities/tags")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tags"] == ["diesel", "winter-ready"]
    assert body["has_more"] is False


def test_tags_search_filters_by_substring(client):
    client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["winter-ready", "diesel"]})

    resp = client.get("/entities/tags", params={"q": "wint"})
    assert resp.json()["tags"] == ["winter-ready"]

    resp = client.get("/entities/tags", params={"q": "zzz-nonexistent"})
    assert resp.json()["tags"] == []


def test_tags_pagination_has_more(client):
    for tag in ["alpha", "bravo", "charlie", "delta"]:
        client.post("/entities", json={**VEHICLE_PAYLOAD, "name": f"Truck {tag}", "tags": [tag]})

    first = client.get("/entities/tags", params={"limit": 2}).json()
    assert first["tags"] == ["alpha", "bravo"]
    assert first["has_more"] is True

    second = client.get("/entities/tags", params={"limit": 2, "offset": 2}).json()
    assert second["tags"] == ["charlie", "delta"]
    assert second["has_more"] is False


def test_tags_respects_domain_filter(client):
    client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["four-wheel-drive"]})
    client.post("/entities", json={**PERSON_PAYLOAD, "tags": ["family"]})

    vehicle_tags = client.get("/entities/tags", params={"domain": "vehicle"}).json()["tags"]
    assert vehicle_tags == ["four-wheel-drive"]

    person_tags = client.get("/entities/tags", params={"domain": "person"}).json()["tags"]
    assert person_tags == ["family"]


def test_tags_excludes_archived_by_default(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["retiring-soon"]}).json()["id"]
    client.post(f"/entities/{entity_id}/archive")

    assert client.get("/entities/tags").json()["tags"] == []
    assert client.get("/entities/tags", params={"include_archived": "true"}).json()["tags"] == [
        "retiring-soon"
    ]
