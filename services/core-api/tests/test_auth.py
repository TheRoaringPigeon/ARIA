from app.config import settings


def test_login_success_sets_cookie(raw_client):
    resp = raw_client.post("/auth/login", json={"password": settings.admin_password})
    assert resp.status_code == 200
    assert "aria_session" in resp.cookies
    body = resp.json()
    assert body["user_name"] == settings.seed_user_name
    assert body["role"] == "owner"


def test_login_wrong_password_rejected(raw_client):
    resp = raw_client.post("/auth/login", json={"password": "definitely-wrong"})
    assert resp.status_code == 401


def test_me_requires_session(raw_client):
    resp = raw_client.get("/auth/me")
    assert resp.status_code == 401


def test_me_after_login(raw_client):
    raw_client.post("/auth/login", json={"password": settings.admin_password})
    resp = raw_client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_name"] == settings.seed_user_name
    assert resp.json()["role"] == "owner"


def test_logout_clears_session(raw_client):
    raw_client.post("/auth/login", json={"password": settings.admin_password})
    logout_resp = raw_client.post("/auth/logout")
    assert logout_resp.status_code == 200

    me_resp = raw_client.get("/auth/me")
    assert me_resp.status_code == 401
