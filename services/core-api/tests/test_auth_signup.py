from app.config import settings


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
