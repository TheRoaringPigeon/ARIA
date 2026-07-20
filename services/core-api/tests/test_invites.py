from datetime import datetime, timedelta, timezone

from tests.conftest import set_session_role


def _signup(raw_client, email="owner-c@example.com"):
    resp = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household C",
            "name": "Owner C",
            "email": email,
            "password": "hunter22",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_create_and_accept_invite_happy_path(raw_client):
    owner = _signup(raw_client)

    invite_resp = raw_client.post("/households/invites")
    assert invite_resp.status_code == 201
    token = invite_resp.json()["token"]

    accept_resp = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "Member C", "email": "member-c@example.com", "password": "hunter22"},
    )
    assert accept_resp.status_code == 201
    body = accept_resp.json()
    assert body["household_id"] == owner["household_id"]
    assert body["role"] == "member"


def test_accept_invite_consumes_token(raw_client):
    _signup(raw_client)
    token = raw_client.post("/households/invites").json()["token"]

    first = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "M1", "email": "m1@example.com", "password": "hunter22"},
    )
    assert first.status_code == 201

    second = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "M2", "email": "m2@example.com", "password": "hunter22"},
    )
    assert second.status_code == 404


async def test_expired_invite_rejected(raw_client, mock_db):
    _signup(raw_client)
    token = raw_client.post("/households/invites").json()["token"]

    await mock_db.invites.update_one(
        {"_id": token}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}}
    )

    resp = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "Late", "email": "late@example.com", "password": "hunter22"},
    )
    assert resp.status_code == 410


def test_accept_invite_duplicate_email_rejected(raw_client):
    _signup(raw_client)
    token = raw_client.post("/households/invites").json()["token"]

    resp = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "Dup", "email": "owner-c@example.com", "password": "x"},
    )
    assert resp.status_code == 409


def test_non_owner_cannot_create_invite(client):
    set_session_role("member")
    resp = client.post("/households/invites")
    assert resp.status_code == 403


def test_revoke_invite(raw_client):
    _signup(raw_client)
    token = raw_client.post("/households/invites").json()["token"]

    revoke_resp = raw_client.delete(f"/households/invites/{token}")
    assert revoke_resp.status_code == 204

    accept_resp = raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "Late", "email": "late@example.com", "password": "hunter22"},
    )
    assert accept_resp.status_code == 404


def test_list_members_includes_owner_and_invited_member(raw_client):
    owner = _signup(raw_client)
    token = raw_client.post("/households/invites").json()["token"]
    raw_client.post(
        "/auth/accept-invite",
        json={"token": token, "name": "Member C", "email": "member-c@example.com", "password": "hunter22"},
    )

    members_resp = raw_client.get("/households/members")
    assert members_resp.status_code == 200
    emails = {m["email"] for m in members_resp.json()}
    assert "member-c@example.com" in emails
    assert owner["user_id"] in {m["id"] for m in members_resp.json()}
