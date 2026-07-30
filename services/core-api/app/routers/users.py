from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.dependencies import SessionContext, get_current_session, get_db_dep
from aria_shared.models import Role

router = APIRouter(prefix="/users", tags=["users"])


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: Role
    theme: str | None = None
    pinned_entity_ids: list[str] = []


class UserUpdate(BaseModel):
    theme: str | None = None
    pinned_entity_ids: list[str] | None = None


def _to_response(user: dict) -> UserResponse:
    return UserResponse(
        id=user["_id"],
        name=user["name"],
        email=user["email"],
        role=user["role"],
        theme=user.get("theme"),
        pinned_entity_ids=user.get("pinned_entity_ids", []),
    )


@router.get("/me", response_model=UserResponse)
async def get_my_user(
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> UserResponse:
    """Live per-user settings (currently just `theme`) distinct from the
    session cookie's `SessionContext` — that's a snapshot taken at login
    (see `aria_auth.create_session`), so it's the wrong place for a value
    that changes mid-session and should show up on every other device/tab
    without waiting for re-login, same reasoning `households.py`'s
    `/households/me` already applies to `city`.
    """
    user = await db.users.find_one({"_id": session.user_id})
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return _to_response(user)


@router.patch("/me", response_model=UserResponse)
async def update_my_user(
    body: UserUpdate,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> UserResponse:
    """Self-service, not owner-gated — unlike `update_my_household`, every
    member manages their own theme. `model_fields_set` (not `model_dump()`)
    so an omitted field is a no-op rather than clearing it, same convention
    `update_my_household` uses for `city`.
    """
    user = await db.users.find_one({"_id": session.user_id})
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    if "theme" in body.model_fields_set:
        user["theme"] = body.theme
        await db.users.update_one({"_id": session.user_id}, {"$set": {"theme": body.theme}})

    if "pinned_entity_ids" in body.model_fields_set:
        # `pinned_entity_ids` itself is never optional on the stored model
        # (`User.pinned_entity_ids: list[str]`) — an explicit `null` here is
        # treated the same as an explicit `[]`, both clearing the list,
        # rather than writing a `None` that would violate that invariant.
        ids = body.pinned_entity_ids or []
        user["pinned_entity_ids"] = ids
        await db.users.update_one({"_id": session.user_id}, {"$set": {"pinned_entity_ids": ids}})

    return _to_response(user)
