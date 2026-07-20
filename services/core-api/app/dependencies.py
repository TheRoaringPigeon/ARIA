from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_db
from aria_auth import (
    SESSION_COOKIE_NAME,
    SessionContext,
    build_get_current_session,
    check_permission,
    has_shared_access,
)


def get_db_dep() -> AsyncIOMotorDatabase:
    return get_db()


# Bound once to this service's own DB accessor — every router imports this
# exact function object, so `app.dependency_overrides[get_current_session]`
# in tests intercepts all of them at once. See aria_auth.build_get_current_session.
get_current_session = build_get_current_session(get_db_dep)


async def require_owner(session: SessionContext = Depends(get_current_session)) -> SessionContext:
    """Gate for household-management routes (invites, member listing) that
    aren't an entity-domain action at all — `check_permission()`'s
    `(EntityDomain | None, Action)` shape exists for entity/log/schedule
    mutations, and forcing this through it would mean inventing a fake
    `domain=None` entry that reads as an entity permission to anyone
    skimming `permissions.py` later. This says exactly what it means at the
    one call site that needs it.
    """
    if session.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the household owner may do this")
    return session


def require_entity_for_create(get_body: Callable[..., Any]):
    """Dependency factory: resolve a create body's `entity_id` (404 if
    missing) and check the caller's role against that entity's domain (403
    if disallowed) — a brand-new log/schedule has no domain of its own to
    check permissions against until it's tied to an entity. Shared by any
    create route whose body carries an `entity_id` (logs, schedules).

    Also checks sharing: a member can't log/schedule against an entity they
    can't otherwise see or edit, even if it belongs to their own household —
    404s on that failure (not 403), same "don't confirm existence to
    someone who can't see it" convention every other sharing check in this
    module follows; only the role-based `check_permission` failure above is
    a 403, since knowing your *own role* was insufficient reveals nothing
    about the record itself.

    `get_body` must be the same function object the route handler uses for
    its own `body` parameter (e.g. `Depends(get_body)` on both) — FastAPI
    caches a dependency's result per callable within a request, so passing
    the same one here means the body is parsed once, not once per
    dependency that needs it.
    """

    async def _require_entity_for_create(
        body: Any = Depends(get_body),
        session: SessionContext = Depends(get_current_session),
        db: AsyncIOMotorDatabase = Depends(get_db_dep),
    ) -> dict:
        entity_doc = await db.entities.find_one(
            {"_id": body.entity_id, "household_id": session.household_id}
        )
        if entity_doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        check_permission(session.role, entity_doc["domain"], "create")
        # .get(), not [] — an entity created before `shared_with` existed
        # has no such key in its stored document at all (only the Pydantic
        # model's default applies, and only when actually validated through
        # it); missing means "household", exactly like the field's own
        # default, same defensive pattern aria_auth.session already uses
        # for a pre-role-tracking session's missing `role` key.
        if not has_shared_access(
            session, entity_doc.get("shared_with", "household"), entity_doc["created_by"]
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        return entity_doc

    return _require_entity_for_create


async def require_entity_access(
    db: AsyncIOMotorDatabase, session: SessionContext, entity_id: str
) -> None:
    """Logs/schedules carry no `shared_with` of their own — access to them
    is always derived from their parent entity, looked up live (not copied
    at creation time, unlike `domain`) so a log/schedule's visibility
    always reflects the entity's *current* sharing, not a stale snapshot.
    Shared by `logs.py` and `schedules.py`'s `require_log`/`require_schedule`
    and `list_entity_logs`/`list_entity_schedules`.
    """
    entity_doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
    if entity_doc is None or not has_shared_access(
        session, entity_doc.get("shared_with", "household"), entity_doc["created_by"]
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")


async def validate_shared_with(
    db: AsyncIOMotorDatabase, household_id: str, shared_with: str | list[str]
) -> None:
    """The server-side half of "only with members in the same household" —
    not just a UI affordance that only lists same-household members, but an
    invariant enforced here regardless of what a client actually sends, the
    same defense-in-depth stance `require_entity_for_create` already takes
    re-checking an entity's household rather than trusting the client's
    `entity_id`. No-op when sharing the whole household (nothing to check).
    """
    if shared_with == "household":
        return
    count = await db.users.count_documents({"_id": {"$in": shared_with}, "household_id": household_id})
    if count != len(shared_with):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "shared_with may only include members of your own household",
        )


__all__ = [
    "get_db_dep",
    "SessionContext",
    "get_current_session",
    "SESSION_COOKIE_NAME",
    "require_entity_for_create",
    "require_entity_access",
    "require_owner",
    "validate_shared_with",
]
