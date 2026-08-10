VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create(client, name, status="active"):
    payload = {**VEHICLE_PAYLOAD, "name": name, "status": status}
    return client.post("/entities", json=payload).json()["id"]


def test_list_entities_has_more_flips_across_pages(client):
    ids = {_create(client, f"Truck {n:02d}") for n in range(5)}

    first = client.get("/entities", params={"limit": 3}).json()
    assert len(first["items"]) == 3
    assert first["has_more"] is True

    second = client.get("/entities", params={"limit": 3, "offset": 3}).json()
    assert len(second["items"]) == 2
    assert second["has_more"] is False

    seen = [e["id"] for e in first["items"]] + [e["id"] for e in second["items"]]
    # Stable name+_id sort means walking every page hits each entity exactly
    # once — no duplicate or skipped row across the page boundary.
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) >= ids


def test_list_entities_sort_is_alphabetical_by_name(client):
    _create(client, "Charlie Truck")
    _create(client, "Alpha Truck")
    _create(client, "Bravo Truck")

    items = client.get("/entities", params={"limit": 100}).json()["items"]
    names = [e["name"] for e in items]
    assert names == sorted(names)


def test_status_filter_is_server_side(client):
    active_id = _create(client, "Active Truck", status="active")
    sold_id = _create(client, "Sold Truck", status="sold")

    active_only = client.get("/entities", params={"status": "active"}).json()["items"]
    ids = {e["id"] for e in active_only}
    assert active_id in ids
    assert sold_id not in ids

    sold_only = client.get("/entities", params={"status": "sold"}).json()["items"]
    ids = {e["id"] for e in sold_only}
    assert sold_id in ids
    assert active_id not in ids


def test_status_filter_combined_with_limit_does_not_silently_drop_matches(client):
    # Regression guard for the bug this endpoint's pagination fixes: before
    # `status` was a server-side filter, a status match past the first page
    # would be silently invisible to any caller that only fetched page one.
    for n in range(4):
        _create(client, f"Active {n}", status="active")
    late_match = _create(client, "Late Sold", status="sold")

    first_page = client.get("/entities", params={"status": "sold", "limit": 2}).json()
    assert [e["id"] for e in first_page["items"]] == [late_match]
    assert first_page["has_more"] is False
