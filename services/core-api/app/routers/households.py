import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr, field_validator

from app.config import settings
from app.dependencies import SessionContext, get_current_session, get_db_dep, require_owner
from app.ids import new_id
from app.routers.auth import SessionResponse, _log_in_user
from aria_auth import hash_password
from aria_shared.models import Role

# No shared `prefix=` — `/auth/accept-invite` deliberately lives on the
# `/auth` path even though its handler is defined in this file (it's
# household-scoped the same way every other route here is, just
# unauthenticated the same way `/auth/login` is), while everything else is
# under `/households`. Each route below spells out its full path instead.
router = APIRouter(tags=["households"])


class InviteResponse(BaseModel):
    token: str
    expires_at: datetime


class MemberResponse(BaseModel):
    id: str
    name: str
    email: str
    role: Role


class HouseholdResponse(BaseModel):
    id: str
    name: str
    city: str | None = None


class HouseholdUpdate(BaseModel):
    city: str | None = None

    @field_validator("city")
    @classmethod
    def _blank_city_is_none(cls, value: str | None) -> str | None:
        # A blank/whitespace-only city must clear the default, not persist
        # as `""` — chat's weather-location fallback (ai-service's
        # `research_node`) treats an empty string as neither set nor
        # cleanly unset, wasting a lookup instead of skipping it cleanly
        # (caught in code review of the M10 signup path this endpoint now
        # also lets an owner edit after the fact).
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AcceptInviteRequest(BaseModel):
    token: str
    name: str
    email: EmailStr
    password: str


@router.post(
    "/households/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED
)
async def create_invite(
    session: SessionContext = Depends(require_owner),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> InviteResponse:
    """Only `role: "member"` invites — an owner can't mint another owner
    this way. A household always has exactly one owner: whoever signed up.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.invite_ttl_hours)
    await db.invites.insert_one(
        {
            "_id": token,
            "household_id": session.household_id,
            "role": "member",
            "created_by_user_id": session.user_id,
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    return InviteResponse(token=token, expires_at=expires_at)


@router.get("/households/invites", response_model=list[InviteResponse])
async def list_invites(
    session: SessionContext = Depends(require_owner),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[InviteResponse]:
    docs = await db.invites.find({"household_id": session.household_id}).to_list(length=None)
    return [InviteResponse(token=doc["_id"], expires_at=doc["expires_at"]) for doc in docs]


@router.delete("/households/invites/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    token: str,
    session: SessionContext = Depends(require_owner),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> Response:
    result = await db.invites.delete_one({"_id": token, "household_id": session.household_id})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/households/me", response_model=HouseholdResponse)
async def get_my_household(
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> HouseholdResponse:
    """The one place `ai-service` learns the caller's household `city` —
    everything else it needs (entities, logs, schedules, documents) already
    flows through the session cookie without ever exposing raw household
    fields. Used to resolve chat's default weather location (M10).
    """
    household = await db.households.find_one({"_id": session.household_id})
    if household is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "household not found")
    return HouseholdResponse(id=household["_id"], name=household["name"], city=household.get("city"))


@router.patch("/households/me", response_model=HouseholdResponse)
async def update_my_household(
    body: HouseholdUpdate,
    session: SessionContext = Depends(require_owner),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> HouseholdResponse:
    """Owner-only — same ownership boundary `create_invite`/`list_invites`
    already enforce for household-level settings, not an entity-domain
    action `check_permission()` would apply to. Currently just `city`
    (the M10 signup field), now editable after the fact instead of being
    signup-only. `model_fields_set` (not `model_dump()`) so an omitted
    `city` in the request body is a no-op rather than clearing it.
    """
    household = await db.households.find_one({"_id": session.household_id})
    if household is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "household not found")

    if "city" in body.model_fields_set:
        household["city"] = body.city
        household["updated_at"] = datetime.now(timezone.utc)
        await db.households.update_one(
            {"_id": session.household_id},
            {"$set": {"city": household["city"], "updated_at": household["updated_at"]}},
        )

    return HouseholdResponse(id=household["_id"], name=household["name"], city=household.get("city"))


@router.get("/households/members", response_model=list[MemberResponse])
async def list_members(
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[MemberResponse]:
    docs = await db.users.find({"household_id": session.household_id}).to_list(length=None)
    return [
        MemberResponse(id=doc["_id"], name=doc["name"], email=doc["email"], role=doc["role"])
        for doc in docs
    ]


@router.post("/auth/accept-invite", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def accept_invite(
    body: AcceptInviteRequest,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> SessionResponse:
    """Public (no session required) — same reasoning `/auth/login` already
    applies to being unauthenticated despite being household-scoped.
    Single-use: the invite doc is deleted once consumed, matching this
    codebase's preference for real deletes over soft `used_at` state where
    nothing needs the history (M1's real `DELETE`, M8's TTL'd checkpoints).
    """
    invite = await db.invites.find_one({"_id": body.token})
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")

    expires_at = invite["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        await db.invites.delete_one({"_id": body.token})
        raise HTTPException(status.HTTP_410_GONE, "invite expired")

    if await db.users.find_one({"email": body.email}) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    now = datetime.now(timezone.utc)
    user = {
        "_id": new_id(),
        "household_id": invite["household_id"],
        "name": body.name,
        "email": body.email,
        "password_hash": hash_password(body.password),
        "role": invite["role"],
        "created_at": now,
    }
    await db.users.insert_one(user)
    await db.invites.delete_one({"_id": body.token})

    return await _log_in_user(user, response, db)
