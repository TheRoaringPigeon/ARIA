from datetime import datetime, timezone

from tests.conftest import TEST_HOUSEHOLD_ID, TEST_USER_ID, TEST_USER_NAME


async def _seed_user(mock_db, **overrides):
    user = {
        "_id": TEST_USER_ID,
        "household_id": TEST_HOUSEHOLD_ID,
        "name": TEST_USER_NAME,
        "email": "test-user@example.com",
        "password_hash": "irrelevant",
        "role": "owner",
        "created_at": datetime.now(timezone.utc),
        **overrides,
    }
    await mock_db.users.insert_one(user)
    return user


def test_get_my_user_requires_session(raw_client):
    resp = raw_client.get("/users/me")
    assert resp.status_code == 401


async def test_get_my_user_returns_no_theme_by_default(client, mock_db):
    await _seed_user(mock_db)
    resp = client.get("/users/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == TEST_USER_ID
    assert body["theme"] is None


async def test_update_my_user_sets_theme(client, mock_db):
    await _seed_user(mock_db)
    resp = client.patch("/users/me", json={"theme": "forest"})
    assert resp.status_code == 200
    assert resp.json()["theme"] == "forest"

    resp = client.get("/users/me")
    assert resp.json()["theme"] == "forest"


async def test_update_my_user_omitted_theme_is_no_op(client, mock_db):
    await _seed_user(mock_db, theme="ocean")
    resp = client.patch("/users/me", json={})
    assert resp.status_code == 200
    assert resp.json()["theme"] == "ocean"


async def test_update_my_user_clears_theme_with_explicit_null(client, mock_db):
    await _seed_user(mock_db, theme="ocean")
    resp = client.patch("/users/me", json={"theme": None})
    assert resp.status_code == 200
    assert resp.json()["theme"] is None


async def test_member_can_update_own_theme(client, mock_db):
    """Unlike `/households/me`, this isn't owner-gated — every member manages
    their own theme."""
    from tests.conftest import set_session_role

    await _seed_user(mock_db, role="member")
    set_session_role("member")
    resp = client.patch("/users/me", json={"theme": "rose"})
    assert resp.status_code == 200
    assert resp.json()["theme"] == "rose"
