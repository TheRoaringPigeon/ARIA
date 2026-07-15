from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_db
from app.session import SESSION_COOKIE_NAME


def get_db_dep() -> AsyncIOMotorDatabase:
    return get_db()


@dataclass
class SessionContext:
    household_id: str
    user_id: str
    user_name: str


async def get_current_session(
    aria_session: str | None = Cookie(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
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
    )


__all__ = ["get_db_dep", "SessionContext", "get_current_session", "SESSION_COOKIE_NAME"]
