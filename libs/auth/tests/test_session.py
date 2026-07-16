from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from aria_auth import SessionContext, build_get_current_session, create_session


@pytest.fixture
def db():
    return AsyncMongoMockClient()["test"]


async def test_create_session_persists_role_and_expiry(db):
    token = await create_session(
        db, user_id="u1", household_id="h1", user_name="Alice", role="owner", ttl_hours=1
    )
    doc = await db.sessions.find_one({"_id": token})
    assert doc["role"] == "owner"
    assert doc["household_id"] == "h1"
    # mongomock round-trips datetimes as naive, same as real Mongo — compare
    # naive-to-naive rather than reusing the aware `datetime.now(timezone.utc)`.
    assert doc["expires_at"] > datetime.now(timezone.utc).replace(tzinfo=None)


async def test_get_current_session_returns_context_for_valid_session(db):
    token = await create_session(
        db, user_id="u1", household_id="h1", user_name="Alice", role="member", ttl_hours=1
    )
    get_current_session = build_get_current_session(lambda: db)

    session = await get_current_session(aria_session=token, db=db)

    assert session == SessionContext(household_id="h1", user_id="u1", user_name="Alice", role="member")


async def test_get_current_session_401_without_cookie(db):
    get_current_session = build_get_current_session(lambda: db)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_session(aria_session=None, db=db)
    assert exc_info.value.status_code == 401


async def test_get_current_session_401_for_unknown_token(db):
    get_current_session = build_get_current_session(lambda: db)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_session(aria_session="does-not-exist", db=db)
    assert exc_info.value.status_code == 401


async def test_get_current_session_401_and_deletes_expired_session(db):
    await db.sessions.insert_one(
        {
            "_id": "expired-token",
            "user_id": "u1",
            "household_id": "h1",
            "user_name": "Alice",
            "role": "owner",
            "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
    )
    get_current_session = build_get_current_session(lambda: db)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_session(aria_session="expired-token", db=db)
    assert exc_info.value.status_code == 401
    assert await db.sessions.find_one({"_id": "expired-token"}) is None


async def test_get_current_session_defaults_role_for_pre_existing_session(db):
    """Sessions issued before role-tracking existed won't have a `role`
    key — default to the least-privileged role rather than KeyError-ing.
    """
    await db.sessions.insert_one(
        {
            "_id": "legacy-token",
            "user_id": "u1",
            "household_id": "h1",
            "user_name": "Alice",
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    get_current_session = build_get_current_session(lambda: db)

    session = await get_current_session(aria_session="legacy-token", db=db)

    assert session.role == "member"
