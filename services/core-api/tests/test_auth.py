from app.config import settings


def _seed_login(raw_client):
    return raw_client.post(
        "/auth/login", json={"email": settings.seed_user_email, "password": settings.admin_password}
    )


def test_login_success_sets_cookie(raw_client):
    resp = _seed_login(raw_client)
    assert resp.status_code == 200
    assert "aria_session" in resp.cookies
    body = resp.json()
    assert body["user_name"] == settings.seed_user_name
    assert body["role"] == "owner"


def test_login_wrong_password_rejected(raw_client):
    resp = raw_client.post(
        "/auth/login", json={"email": settings.seed_user_email, "password": "definitely-wrong"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_rejected(raw_client):
    resp = raw_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "anything"}
    )
    assert resp.status_code == 401


async def test_login_rejects_not_crashes_on_pre_migration_user_missing_password_hash(
    raw_client, mock_db
):
    """Regression test: hit live against the persistent dev stack — a
    household seeded before `password_hash` existed on `User` has no such
    field at all, and `login()` used to raise an uncaught `KeyError` (500)
    instead of a clean 401. `ensure_seed_household` only inserts a user if
    none exists yet, so an already-seeded pre-migration household is never
    retroactively fixed by it.
    """
    await mock_db.users.update_one(
        {"email": settings.seed_user_email}, {"$unset": {"password_hash": ""}}
    )

    resp = _seed_login(raw_client)

    assert resp.status_code == 401


def test_me_requires_session(raw_client):
    resp = raw_client.get("/auth/me")
    assert resp.status_code == 401


def test_me_after_login(raw_client):
    _seed_login(raw_client)
    resp = raw_client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_name"] == settings.seed_user_name
    assert resp.json()["role"] == "owner"


def test_logout_clears_session(raw_client):
    _seed_login(raw_client)
    logout_resp = raw_client.post("/auth/logout")
    assert logout_resp.status_code == 200

    me_resp = raw_client.get("/auth/me")
    assert me_resp.status_code == 401
