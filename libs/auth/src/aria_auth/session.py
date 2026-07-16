import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Cookie, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from aria_shared.models import Role

SESSION_COOKIE_NAME = "aria_session"


@dataclass
class SessionContext:
    household_id: str
    user_id: str
    user_name: str
    role: Role


async def create_session(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    household_id: str,
    user_name: str,
    role: Role,
    ttl_hours: int,
) -> str:
    """Issue and persist a new session, returning its token.

    This is the one seam a future OAuth/OIDC login (e.g. Keycloak) calls
    into after validating its own token — session issuance doesn't care how
    the caller was authenticated, so swapping the auth mechanism later only
    means adding a new caller of this function, not changing it or anything
    downstream that reads sessions. `ttl_hours` is a parameter rather than a
    setting read here, since each service owns its own config.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.sessions.insert_one(
        {
            "_id": token,
            "user_id": user_id,
            "household_id": household_id,
            "user_name": user_name,
            "role": role,
            "created_at": now,
            "expires_at": now + timedelta(hours=ttl_hours),
        }
    )
    return token


def build_get_current_session(
    get_db: Callable[[], AsyncIOMotorDatabase],
) -> Callable[..., "SessionContext"]:
    """Build a `get_current_session` FastAPI dependency bound to one
    service's own DB accessor.

    This package has no way to reach into any particular service's own
    Motor client singleton, so each service calls this once — e.g.
    `get_current_session = build_get_current_session(get_db_dep)` in its
    `app/dependencies.py` — and every router imports that one bound
    function. `app.dependency_overrides` in tests then only has to target
    that single object, no matter how many routes depend on it.
    """

    async def get_current_session(
        aria_session: str | None = Cookie(default=None),
        db: AsyncIOMotorDatabase = Depends(get_db),
    ) -> SessionContext:
        if aria_session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")

        session_doc = await db.sessions.find_one({"_id": aria_session})
        if session_doc is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session invalid")

        expires_at = session_doc["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            await db.sessions.delete_one({"_id": aria_session})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")

        return SessionContext(
            household_id=session_doc["household_id"],
            user_id=session_doc["user_id"],
            user_name=session_doc["user_name"],
            # .get(), not [] — sessions issued before role-tracking existed
            # won't have it; default to the least-privileged role rather
            # than 500ing or silently granting owner.
            role=session_doc.get("role", "member"),
        )

    return get_current_session
