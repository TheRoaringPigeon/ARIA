from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_db
from aria_auth import SESSION_COOKIE_NAME, SessionContext, build_get_current_session, check_permission


def get_db_dep() -> AsyncIOMotorDatabase:
    return get_db()


# Bound once to this service's own DB accessor — every router imports this
# exact function object, so `app.dependency_overrides[get_current_session]`
# in tests intercepts all of them at once. See aria_auth.build_get_current_session.
get_current_session = build_get_current_session(get_db_dep)


def require_entity_for_create(get_body: Callable[..., Any]):
    """Dependency factory: resolve a create body's `entity_id` (404 if
    missing) and check the caller's role against that entity's domain (403
    if disallowed) — a brand-new log/schedule has no domain of its own to
    check permissions against until it's tied to an entity. Shared by any
    create route whose body carries an `entity_id` (logs, schedules).

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
        return entity_doc

    return _require_entity_for_create


__all__ = [
    "get_db_dep",
    "SessionContext",
    "get_current_session",
    "SESSION_COOKIE_NAME",
    "require_entity_for_create",
]
