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

    # users.email: unique — enforces at the database level what the
    # application-level check-then-insert in routers/auth.py and
    # routers/households.py (signup, accept-invite) can only do
    # probabilistically. Also the index that makes /auth/login and
    # /auth/signup's duplicate check an index lookup instead of a full
    # collection scan (the single highest-frequency query path in the app).
    await db.users.create_index("email", unique=True)
    # Household member listing (routers/households.py list_members).
    await db.users.create_index("household_id")
    # worker's daily overdue-digest task scans for opted-in users; most
    # users have this False, so a partial index only covers the ones that
    # actually matter.
    await db.users.create_index(
        "notify_overdue_email",
        partialFilterExpression={"notify_overdue_email": True},
    )

    # logs: entity-scoped history (list_entity_logs, PDF export) and the
    # schedule-resync lookup (_resync_schedule, runs on every log
    # create/update/delete with a schedule_id) are two distinct access
    # patterns needing two distinct compound indexes — neither is a prefix
    # of the other.
    await db.logs.create_index([("household_id", 1), ("entity_id", 1), ("occurred_at", -1)])
    await db.logs.create_index([("household_id", 1), ("schedule_id", 1), ("occurred_at", -1)])

    # schedules: entity-scoped list (list_entity_schedules) is a separate
    # access pattern from the due-soon/calendar/digest shape below — the
    # latter's leading fields (active, pending_delete_at, interval_type)
    # aren't present on the former's query at all, so one compound index
    # can't serve both.
    await db.schedules.create_index([("household_id", 1), ("entity_id", 1)])
    # Serves three call sites that all share this exact equality-filter
    # shape: GET /schedules/due-soon, GET /schedules/calendar (core-api),
    # and worker's send_overdue_digest — household_id/active/
    # pending_delete_at/interval_type are equality filters on every one of
    # them; next_due_at trails as the field due-soon/the digest task also
    # range-filter and sort on (calendar doesn't filter on it, but a
    # trailing extra field is harmless when unused).
    await db.schedules.create_index(
        [
            ("household_id", 1),
            ("active", 1),
            ("pending_delete_at", 1),
            ("interval_type", 1),
            ("next_due_at", 1),
        ]
    )

    # documents: entity_ids is many-to-many (array field) — Mongo indexes
    # this as a multikey index automatically, no special syntax needed.
    # Serves list_entity_documents and the PDF-export document pull.
    await db.documents.create_index(
        [("household_id", 1), ("entity_ids", 1), ("uploaded_at", -1)]
    )
