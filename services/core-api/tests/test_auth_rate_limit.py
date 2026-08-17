import app.redis_client as redis_client_module
from app.config import settings
from tests.conftest import _BrokenRedis


def _login(raw_client, email, password, forwarded_for=None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return raw_client.post(
        "/auth/login", json={"email": email, "password": password}, headers=headers
    )


def _signup(raw_client, email, forwarded_for=None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return raw_client.post(
        "/auth/signup",
        json={"household_name": "H", "name": "N", "email": email, "password": "hunter22"},
        headers=headers,
    )


def test_login_blocked_after_n_failed_attempts_same_ip(raw_client):
    for _ in range(settings.login_rate_limit_attempts):
        resp = _login(raw_client, settings.seed_user_email, "wrong-password")
        assert resp.status_code == 401

    resp = _login(raw_client, settings.seed_user_email, "wrong-password")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_login_still_401_below_the_limit(raw_client):
    resp = _login(raw_client, settings.seed_user_email, "wrong-password")
    assert resp.status_code == 401


def test_login_blocked_by_email_counter_even_from_different_ips(raw_client):
    for i in range(settings.login_rate_limit_attempts):
        resp = _login(raw_client, settings.seed_user_email, "wrong-password", forwarded_for=f"10.0.0.{i}")
        assert resp.status_code == 401

    resp = _login(
        raw_client, settings.seed_user_email, "wrong-password", forwarded_for="10.0.0.255"
    )
    assert resp.status_code == 429


def test_login_success_does_not_increment_counter(raw_client):
    for _ in range(settings.login_rate_limit_attempts + 5):
        resp = _login(raw_client, settings.seed_user_email, settings.admin_password)
        assert resp.status_code == 200
        raw_client.post("/auth/logout")


def test_signup_blocked_after_n_attempts_from_same_ip(raw_client):
    for i in range(settings.signup_rate_limit_attempts):
        resp = _signup(raw_client, f"spam-{i}@example.com")
        assert resp.status_code == 201
        raw_client.post("/auth/logout")

    resp = _signup(raw_client, "one-too-many@example.com")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_signup_duplicate_email_attempt_still_consumes_quota(raw_client):
    _signup(raw_client, "dup-quota@example.com")
    raw_client.post("/auth/logout")

    # Mix a duplicate-email 409 in with fresh signups — the 409 still
    # consumes quota, so the limit is still hit at exactly the configured
    # count regardless of how many of those attempts failed vs. succeeded.
    for i in range(settings.signup_rate_limit_attempts - 1):
        resp = _signup(raw_client, "dup-quota@example.com")
        assert resp.status_code == 409

    resp = _signup(raw_client, "fresh-after-limit@example.com")
    assert resp.status_code == 429


def test_login_fails_open_when_redis_unreachable(raw_client, monkeypatch):
    monkeypatch.setattr(redis_client_module, "_client", _BrokenRedis())

    bad_resp = _login(raw_client, settings.seed_user_email, "wrong-password")
    assert bad_resp.status_code == 401

    good_resp = _login(raw_client, settings.seed_user_email, settings.admin_password)
    assert good_resp.status_code == 200


def test_signup_fails_open_when_redis_unreachable(raw_client, monkeypatch):
    monkeypatch.setattr(redis_client_module, "_client", _BrokenRedis())

    resp = _signup(raw_client, "fail-open@example.com")
    assert resp.status_code == 201
