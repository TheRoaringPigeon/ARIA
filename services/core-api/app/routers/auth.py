from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr

from app.config import settings
from app.dependencies import SessionContext, get_current_session, get_db_dep
from app.ids import new_id
from aria_auth import SESSION_COOKIE_NAME, create_session, hash_password, verify_password
from aria_shared.models import Role

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    household_name: str
    name: str
    email: EmailStr
    password: str


class SessionResponse(BaseModel):
    household_id: str
    user_id: str
    user_name: str
    role: Role


async def _log_in_user(user: dict, response: Response, db: AsyncIOMotorDatabase) -> SessionResponse:
    """Shared by `/login`, `/signup`, and `households.py`'s `/auth/accept-invite`
    — the only thing that differs between them is how `user` was resolved/
    created; session issuance and cookie-setting are identical everywhere.
    """
    token = await create_session(
        db,
        user_id=user["_id"],
        household_id=user["household_id"],
        user_name=user["name"],
        role=user["role"],
        ttl_hours=settings.session_ttl_hours,
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return SessionResponse(
        household_id=user["household_id"],
        user_id=user["_id"],
        user_name=user["name"],
        role=user["role"],
    )


@router.post("/login", response_model=SessionResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> SessionResponse:
    """Real per-user login: every household's users are distinguished by
    email+password, not a single shared household password. Session
    creation/cookie-setting still goes through the same `create_session()`
    seam a future Keycloak/OIDC callback would use too.
    """
    user = await db.users.find_one({"email": body.email})
    # .get(), not [] — a user document from before this field existed (a
    # real case hit live: a dev household seeded pre-M9) has no
    # password_hash at all; verify_password() itself also degrades safely
    # on `None`, but resolving it here up front means this call site works
    # even against a raw dict shape from an older schema version.
    if user is None or not verify_password(body.password, user.get("password_hash")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")

    return await _log_in_user(user, response, db)


@router.post("/signup", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> SessionResponse:
    """Create a brand-new household + its owner user, auto-logging in on
    success — the general path for a new household to exist at all.
    `ensure_seed_household` (app/seed.py) is just this same shape, run once
    at startup so there's always something to log into out of the box.
    """
    if await db.users.find_one({"email": body.email}) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    now = datetime.now(timezone.utc)
    household_id = new_id()
    user_id = new_id()

    await db.households.insert_one(
        {
            "_id": household_id,
            "name": body.household_name,
            "created_at": now,
            "updated_at": now,
        }
    )
    user = {
        "_id": user_id,
        "household_id": household_id,
        "name": body.name,
        "email": body.email,
        "password_hash": hash_password(body.password),
        "role": "owner",
        "created_at": now,
    }
    await db.users.insert_one(user)

    return await _log_in_user(user, response, db)


@router.get("/me", response_model=SessionResponse)
async def me(session: SessionContext = Depends(get_current_session)) -> SessionResponse:
    return SessionResponse(
        household_id=session.household_id,
        user_id=session.user_id,
        user_name=session.user_name,
        role=session.role,
    )


@router.post("/logout")
async def logout(
    response: Response,
    aria_session: str | None = Cookie(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> dict:
    if aria_session is not None:
        await db.sessions.delete_one({"_id": aria_session})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}
