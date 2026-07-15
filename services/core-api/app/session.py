import secrets
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings

SESSION_COOKIE_NAME = "aria_session"


async def create_session(
    db: AsyncIOMotorDatabase, *, user_id: str, household_id: str, user_name: str
) -> str:
    """Issue and persist a new session, returning its token.

    This is the one seam a future OAuth/OIDC login (e.g. Keycloak) calls
    into after validating its own token — session issuance doesn't care how
    the caller was authenticated, so swapping the auth mechanism later only
    means adding a new caller of this function, not changing it or anything
    downstream that reads sessions.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.sessions.insert_one(
        {
            "_id": token,
            "user_id": user_id,
            "household_id": household_id,
            "user_name": user_name,
            "created_at": now,
            "expires_at": now + timedelta(hours=settings.session_ttl_hours),
        }
    )
    return token
