from motor.motor_asyncio import AsyncIOMotorDatabase


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Every entity query filters by `household_id` first (see
    `routers/entities.py`'s `list_entities`/`get_entity`/etc.) — with no
    index at all, that's a full collection scan across every household's
    documents just to find this one's, not just a scan of this household's
    own (usually small) subset.

    Indexes are collection-wide, not per-tenant — one compound index here
    covers every household's lookups, past and future, so this runs once at
    service startup (same seam `ensure_seed_household` already uses)
    instead of being tied to household creation. `create_index` is
    idempotent — a no-op if an equivalent index already exists — so it's
    safe to call on every startup, not just the first.

    Field order: `household_id` leads since every query filters on it;
    `archived_at` next since excluding archived records is the default on
    every list call; `domain` last since it's an optional filter layered on
    top of the other two.
    """
    await db.entities.create_index([("household_id", 1), ("archived_at", 1), ("domain", 1)])
