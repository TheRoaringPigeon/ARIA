from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.ids import new_id
from aria_auth import hash_password


async def ensure_seed_household(db: AsyncIOMotorDatabase) -> None:
    """Idempotently create one household/user so there's always something
    to log into out of the box — real households are created via
    `POST /auth/signup` (see app/routers/auth.py).
    """
    existing = await db.users.find_one({"email": settings.seed_user_email})
    if existing is not None:
        return

    now = datetime.now(timezone.utc)
    household_id = new_id()
    user_id = new_id()

    await db.households.insert_one(
        {
            "_id": household_id,
            "name": settings.seed_household_name,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.users.insert_one(
        {
            "_id": user_id,
            "household_id": household_id,
            "name": settings.seed_user_name,
            "email": settings.seed_user_email,
            "password_hash": hash_password(settings.admin_password),
            "role": "owner",
            "created_at": now,
        }
    )
