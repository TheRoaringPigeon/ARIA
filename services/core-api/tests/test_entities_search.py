VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def test_search_matches_by_name(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "Blue Ranger"}).json()["id"]
    assert entity_id in {e["id"] for e in client.get("/entities", params={"q": "Ranger"}).json()}


def test_search_matches_by_tag(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "tags": ["winter-ready"]}).json()["id"]
    assert entity_id in {e["id"] for e in client.get("/entities", params={"q": "winter"}).json()}


def test_search_matches_by_location(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "location": "Garage"}).json()["id"]
    assert entity_id in {e["id"] for e in client.get("/entities", params={"q": "gara"}).json()}


def test_search_matches_by_specs_value(client):
    entity_id = client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "specs": {"color": "Midnight Blue"}}
    ).json()["id"]
    assert entity_id in {e["id"] for e in client.get("/entities", params={"q": "midnight"}).json()}


def test_search_entity_without_specs_not_excluded_by_error(client):
    """Regression guard for the $ifNull in the specs $expr clause — an
    entity with no `specs` key at all must not 500, just not match.
    """
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]
    resp = client.get("/entities", params={"q": "anything"})
    assert resp.status_code == 200
    assert entity_id not in {e["id"] for e in resp.json()}


def test_search_case_insensitive(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "Blue Ranger"}).json()["id"]
    assert entity_id in {e["id"] for e in client.get("/entities", params={"q": "BLUE ranger"}).json()}


def test_search_no_match_returns_empty(client):
    client.post("/entities", json=VEHICLE_PAYLOAD)
    assert client.get("/entities", params={"q": "zzz-nonexistent"}).json() == []


def test_search_respects_domain_and_archived_filters(client):
    entity_id = client.post("/entities", json={**VEHICLE_PAYLOAD, "name": "Archived Ranger"}).json()["id"]
    client.post(f"/entities/{entity_id}/archive")
    assert client.get("/entities", params={"q": "Ranger"}).json() == []
    assert entity_id in {
        e["id"] for e in client.get("/entities", params={"q": "Ranger", "include_archived": "true"}).json()
    }


def _login(raw_client, email, password="hunter22"):
    resp = raw_client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()


def _household_with_two_members(raw_client):
    owner = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household Search Test",
            "name": "Owner",
            "email": "owner-search@example.com",
            "password": "hunter22",
        },
    ).json()
    token_a = raw_client.post("/households/invites").json()["token"]
    member_a = raw_client.post(
        "/auth/accept-invite",
        json={
            "token": token_a,
            "name": "Member A",
            "email": "member-a-search@example.com",
            "password": "hunter22",
        },
    ).json()
    _login(raw_client, "owner-search@example.com")
    token_b = raw_client.post("/households/invites").json()["token"]
    member_b = raw_client.post(
        "/auth/accept-invite",
        json={
            "token": token_b,
            "name": "Member B",
            "email": "member-b-search@example.com",
            "password": "hunter22",
        },
    ).json()
    _login(raw_client, "owner-search@example.com")
    return owner, member_a, member_b


def test_search_respects_sharing_filter(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)
    entity_id = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "name": "Secret Ranger", "shared_with": [member_a["user_id"]]}
    ).json()["id"]

    _login(raw_client, "member-b-search@example.com")
    assert entity_id not in {e["id"] for e in raw_client.get("/entities", params={"q": "Secret"}).json()}

    _login(raw_client, "member-a-search@example.com")
    assert entity_id in {e["id"] for e in raw_client.get("/entities", params={"q": "Secret"}).json()}
