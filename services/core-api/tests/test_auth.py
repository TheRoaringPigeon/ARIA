from app.config import settings


def test_login_success_sets_cookie(client):
    resp = client.post("/auth/login", json={"password": settings.admin_password})
    assert resp.status_code == 200
    assert "aria_session" in resp.cookies
    body = resp.json()
    assert body["user_name"] == settings.seed_user_name


def test_login_wrong_password_rejected(client):
    resp = client.post("/auth/login", json={"password": "definitely-wrong"})
    assert resp.status_code == 401


def test_me_requires_session(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_after_login(client):
    client.post("/auth/login", json={"password": settings.admin_password})
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_name"] == settings.seed_user_name


def test_logout_clears_session(client):
    client.post("/auth/login", json={"password": settings.admin_password})
    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 200

    me_resp = client.get("/auth/me")
    assert me_resp.status_code == 401
