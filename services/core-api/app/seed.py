from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.ids import new_id


async def ensure_seed_household(db: AsyncIOMotorDatabase) -> None:
    """Idempotently create the one household/user M1 supports.

    Real multi-household signup is out of scope until auth grows beyond a
    single shared password (see app/session.py for the OAuth swap seam).
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
            "role": "owner",
            "created_at": now,
        }
    )
