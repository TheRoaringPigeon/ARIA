import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr

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
