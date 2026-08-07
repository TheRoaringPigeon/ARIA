from datetime import datetime, timezone

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


async def test_signup_race_duplicate_email_returns_409_not_500(raw_client, mock_db, monkeypatch):
    """A single-threaded test can't reproduce a real concurrent request, so
    this simulates the race window directly: force the pre-check
    (find_one) to see no conflict while a colliding user already exists,
    proving the users.email unique index (not just the pre-check) is what
    actually prevents two accounts sharing an email — and that losing the
    race surfaces as a clean 409, not an unhandled DuplicateKeyError/500.
    """
    await mock_db.users.insert_one(
        {
            "_id": "existing-racer",
            "household_id": "existing-household",
            "name": "Existing Racer",
            "email": "racer@example.com",
            "password_hash": "irrelevant",
            "role": "owner",
            "created_at": datetime.now(timezone.utc),
        }
    )

    async def _find_one_returns_none(*args, **kwargs):
        return None

    monkeypatch.setattr(mock_db.users, "find_one", _find_one_returns_none)

    resp = _signup(raw_client, email="racer@example.com")
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


def test_owner_can_rename_household(raw_client):
    _signup(raw_client, email="owner-i@example.com")

    resp = raw_client.patch("/households/me", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"

    household_resp = raw_client.get("/households/me")
    assert household_resp.json()["name"] == "New Name"


def test_update_household_blank_name_rejected(raw_client):
    _signup(raw_client, email="owner-j@example.com")

    resp = raw_client.patch("/households/me", json={"name": "   "})
    assert resp.status_code == 422


def test_update_household_null_name_rejected(raw_client):
    _signup(raw_client, email="owner-k@example.com")

    resp = raw_client.patch("/households/me", json={"name": None})
    assert resp.status_code == 422


def test_owner_can_set_household_timezone(raw_client):
    _signup(raw_client, email="owner-l@example.com")

    resp = raw_client.patch("/households/me", json={"timezone": "America/Denver"})
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/Denver"

    household_resp = raw_client.get("/households/me")
    assert household_resp.json()["timezone"] == "America/Denver"


def test_update_household_unrecognized_timezone_rejected(raw_client):
    _signup(raw_client, email="owner-m@example.com")

    resp = raw_client.patch("/households/me", json={"timezone": "Not/AZone"})
    assert resp.status_code == 422


def test_update_household_blank_timezone_clears_it(raw_client):
    _signup(raw_client, email="owner-n@example.com")
    raw_client.patch("/households/me", json={"timezone": "America/Denver"})

    resp = raw_client.patch("/households/me", json={"timezone": "   "})
    assert resp.status_code == 200
    assert resp.json()["timezone"] is None


def test_signup_household_timezone_defaults_to_null(raw_client):
    _signup(raw_client, email="owner-o@example.com")

    household_resp = raw_client.get("/households/me")
    assert household_resp.status_code == 200
    assert household_resp.json()["timezone"] is None
