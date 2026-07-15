import hmac

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.config import settings
from app.dependencies import SessionContext, get_current_session, get_db_dep
from app.session import SESSION_COOKIE_NAME, create_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    household_id: str
    user_id: str
    user_name: str


@router.post("/login", response_model=SessionResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> SessionResponse:
    """Single shared-password login for M1's one household. Password check
    is the only thing this route does that a future Keycloak/OIDC callback
    wouldn't — everything after (session creation, cookie) goes through the
    same create_session() helper that callback would use too.
    """
    if not hmac.compare_digest(body.password, settings.admin_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid password")

    user = await db.users.find_one({"email": settings.seed_user_email})
    if user is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "household not seeded yet")

    token = await create_session(
        db,
        user_id=user["_id"],
        household_id=user["household_id"],
        user_name=user["name"],
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return SessionResponse(
        household_id=user["household_id"], user_id=user["_id"], user_name=user["name"]
    )


@router.get("/me", response_model=SessionResponse)
async def me(session: SessionContext = Depends(get_current_session)) -> SessionResponse:
    return SessionResponse(
        household_id=session.household_id,
        user_id=session.user_id,
        user_name=session.user_name,
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
