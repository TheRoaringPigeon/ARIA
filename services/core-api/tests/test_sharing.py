VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _login(raw_client, email, password="hunter22"):
    resp = raw_client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()


def _household_with_two_members(raw_client):
    """Signs up a fresh household (its owner logged in as the side effect
    of signup) with two invited members, returning each's identity. Ends
    with the owner logged back in, so callers can act as the owner
    immediately without an extra `_login` call.
    """
    owner = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household Sharing Test",
            "name": "Owner",
            "email": "owner-sharing@example.com",
            "password": "hunter22",
        },
    ).json()

    token_a = raw_client.post("/households/invites").json()["token"]
    member_a = raw_client.post(
        "/auth/accept-invite",
        json={"token": token_a, "name": "Member A", "email": "member-a@example.com", "password": "hunter22"},
    ).json()

    _login(raw_client, "owner-sharing@example.com")
    token_b = raw_client.post("/households/invites").json()["token"]
    member_b = raw_client.post(
        "/auth/accept-invite",
        json={"token": token_b, "name": "Member B", "email": "member-b@example.com", "password": "hunter22"},
    ).json()

    _login(raw_client, "owner-sharing@example.com")
    return owner, member_a, member_b


def test_entity_narrowed_to_one_member_hidden_from_another(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    entity = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "shared_with": [member_a["user_id"]]}
    ).json()
    entity_id = entity["id"]

    _login(raw_client, "member-a@example.com")
    assert raw_client.get(f"/entities/{entity_id}").status_code == 200
    assert entity_id in {e["id"] for e in raw_client.get("/entities").json()}

    _login(raw_client, "member-b@example.com")
    assert raw_client.get(f"/entities/{entity_id}").status_code == 404
    assert entity_id not in {e["id"] for e in raw_client.get("/entities").json()}


def test_owner_sees_narrowly_shared_entity_regardless(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    entity_id = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "shared_with": [member_a["user_id"]]}
    ).json()["id"]

    _login(raw_client, "owner-sharing@example.com")
    assert raw_client.get(f"/entities/{entity_id}").status_code == 200
    assert entity_id in {e["id"] for e in raw_client.get("/entities").json()}
    assert raw_client.delete(f"/entities/{entity_id}").status_code == 204


def test_excluded_member_cannot_log_or_schedule_against_entity(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    entity_id = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "shared_with": [member_a["user_id"]]}
    ).json()["id"]

    _login(raw_client, "member-a@example.com")
    log_resp = raw_client.post(
        "/logs", json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "ok"}
    )
    assert log_resp.status_code == 201
    log_id = log_resp.json()["id"]

    _login(raw_client, "member-b@example.com")
    assert (
        raw_client.post(
            "/logs",
            json={"entity_id": entity_id, "type": "note", "occurred_at": "2026-03-01", "title": "nope"},
        ).status_code
        == 404
    )
    assert raw_client.get(f"/entities/{entity_id}/logs").status_code == 404

    _login(raw_client, "member-a@example.com")
    assert raw_client.get(f"/entities/{entity_id}/logs").json()[0]["id"] == log_id


def test_widening_sharing_grants_access_immediately(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    # Member A creates (and so owns) this entity, narrowed to just themselves —
    # only the creator or the household owner may later widen its sharing.
    _login(raw_client, "member-a@example.com")
    entity_id = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "shared_with": [member_a["user_id"]]}
    ).json()["id"]

    assert raw_client.patch(
        f"/entities/{entity_id}", json={"shared_with": [member_a["user_id"], member_b["user_id"]]}
    ).status_code == 200

    _login(raw_client, "member-b@example.com")
    assert raw_client.get(f"/entities/{entity_id}").status_code == 200


def test_non_creator_member_cannot_change_sharing_but_can_edit_other_fields(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    entity_id = raw_client.post("/entities", json={**VEHICLE_PAYLOAD, "shared_with": "household"}).json()[
        "id"
    ]

    _login(raw_client, "member-a@example.com")
    sharing_resp = raw_client.patch(f"/entities/{entity_id}", json={"shared_with": [member_a["user_id"]]})
    assert sharing_resp.status_code == 403

    field_resp = raw_client.patch(f"/entities/{entity_id}", json={"name": "Renamed Truck"})
    assert field_resp.status_code == 200
    assert field_resp.json()["name"] == "Renamed Truck"


def test_validate_shared_with_rejects_user_from_different_household(raw_client):
    _household_with_two_members(raw_client)

    outside_user = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household Outside",
            "name": "Outsider",
            "email": "outsider@example.com",
            "password": "hunter22",
        },
    ).json()

    _login(raw_client, "owner-sharing@example.com")
    resp = raw_client.post(
        "/entities", json={**VEHICLE_PAYLOAD, "shared_with": [outside_user["user_id"]]}
    )
    assert resp.status_code == 400


def test_document_sharing_narrower_than_its_linked_entity(raw_client):
    owner, member_a, member_b = _household_with_two_members(raw_client)

    entity_id = raw_client.post("/entities", json={**VEHICLE_PAYLOAD, "shared_with": "household"}).json()[
        "id"
    ]

    _login(raw_client, "member-a@example.com")
    doc_resp = raw_client.post(
        "/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"document_type": "manual", "entity_ids": [entity_id], "shared_with": [member_a["user_id"]]},
    )
    assert doc_resp.status_code == 201
    document_id = doc_resp.json()["id"]

    _login(raw_client, "member-b@example.com")
    # Member B can see the (household-shared) entity, but not this
    # narrowly-shared document attached to it.
    assert raw_client.get(f"/entities/{entity_id}").status_code == 200
    assert document_id not in {d["id"] for d in raw_client.get(f"/entities/{entity_id}/documents").json()}
    assert raw_client.get(f"/documents/{document_id}").status_code == 404

    _login(raw_client, "member-a@example.com")
    assert document_id in {d["id"] for d in raw_client.get(f"/entities/{entity_id}/documents").json()}
