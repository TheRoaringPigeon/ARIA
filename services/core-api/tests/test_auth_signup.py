from app.config import settings
from tests.conftest import set_session_role


def _signup(raw_client, email="owner-b@example.com"):
    return raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household B",
            "name": "Owner B",
            "email": email,
            "password": "hunter22",
        },
    )


def test_signup_creates_new_household_and_logs_in(raw_client):
    resp = _signup(raw_client)
    assert resp.status_code == 201
    assert "aria_session" in resp.cookies
    body = resp.json()
    assert body["role"] == "owner"
    assert body["user_name"] == "Owner B"

    me_resp = raw_client.get("/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["household_id"] == body["household_id"]


def test_signup_is_a_distinct_household_from_the_seed(raw_client):
    signup_resp = _signup(raw_client)
    signup_household_id = signup_resp.json()["household_id"]

    raw_client.post("/auth/logout")
    seed_login_resp = raw_client.post(
        "/auth/login", json={"email": settings.seed_user_email, "password": settings.admin_password}
    )
    assert seed_login_resp.json()["household_id"] != signup_household_id


def test_signup_duplicate_email_rejected(raw_client):
    _signup(raw_client, email="dup@example.com")
    raw_client.post("/auth/logout")
    resp = _signup(raw_client, email="dup@example.com")
    assert resp.status_code == 409


def test_signup_with_city_stores_it_on_household(raw_client):
    resp = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household D",
            "city": "Lizella, GA",
            "name": "Owner D",
            "email": "owner-d@example.com",
            "password": "hunter22",
        },
    )
    assert resp.status_code == 201

    household_resp = raw_client.get("/households/me")
    assert household_resp.status_code == 200
    assert household_resp.json()["city"] == "Lizella, GA"


def test_signup_without_city_leaves_it_null(raw_client):
    _signup(raw_client, email="owner-e@example.com")

    household_resp = raw_client.get("/households/me")
    assert household_resp.status_code == 200
    assert household_resp.json()["city"] is None


def test_owner_can_update_household_city(raw_client):
    _signup(raw_client, email="owner-f@example.com")

    resp = raw_client.patch("/households/me", json={"city": "Austin, TX"})
    assert resp.status_code == 200
    assert resp.json()["city"] == "Austin, TX"

    household_resp = raw_client.get("/households/me")
    assert household_resp.json()["city"] == "Austin, TX"


def test_update_household_city_blank_string_clears_it(raw_client):
    resp = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household G",
            "city": "Lizella, GA",
            "name": "Owner G",
            "email": "owner-g@example.com",
            "password": "hunter22",
        },
    )
    assert resp.status_code == 201

    resp = raw_client.patch("/households/me", json={"city": "   "})
    assert resp.status_code == 200
    assert resp.json()["city"] is None


def test_update_household_omitted_city_is_a_no_op(raw_client):
    resp = raw_client.post(
        "/auth/signup",
        json={
            "household_name": "Household H",
            "city": "Denver, CO",
            "name": "Owner H",
            "email": "owner-h@example.com",
            "password": "hunter22",
        },
    )
    assert resp.status_code == 201

    resp = raw_client.patch("/households/me", json={})
    assert resp.status_code == 200
    assert resp.json()["city"] == "Denver, CO"


def test_member_cannot_update_household(client):
    set_session_role("member")
    resp = client.patch("/households/me", json={"city": "Nowhere"})
    assert resp.status_code == 403
