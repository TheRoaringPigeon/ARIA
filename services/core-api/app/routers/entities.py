import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ValidationError

from app.celery_client import enqueue_document_deletion
from app.dependencies import SessionContext, get_current_session, get_db_dep, validate_shared_with
from app.ids import new_id
from app.schemas.entities import EntityCreate, EntityUpdate
from aria_auth import Action, check_permission, has_shared_access
from aria_shared.models import EntityBase, EntityDomain

router = APIRouter(prefix="/entities", tags=["entities"])

MAX_LIMIT = 200


def _search_filter(q: str) -> dict:
    """Case-insensitive substring match against `name`/`tags`/`location`,
    plus any value in the free-form `specs` dict.

    `re.escape` — `q` is user input passed straight into `$regex`; without
    escaping, a query like "(" or ".*" either errors Mongo or matches
    everything.

    No operator does "regex against any value of a dict" directly —
    `$objectToArray` turns `specs` into `[{k, v}, ...]` pairs, then
    `$filter` + `$size > 0` checks whether any value matches. `$ifNull`
    covers entities with no `specs` key at all. Deliberately `$regex`, not a
    Mongo text index: this backs a type-ahead search box where "rang"
    should match "Ranger" mid-word — `$text` only matches whole (optionally
    stemmed) tokens, so it wouldn't. No indexes exist anywhere in `core-api`
    yet (single-household data, not multi-tenant scale), so an unindexed
    scan here is an accepted, non-blocking tradeoff.
    """
    pattern = re.escape(q.strip())
    return {
        "$or": [
            {"name": {"$regex": pattern, "$options": "i"}},
            # Mongo's $regex on an array field matches if any element
            # matches — no $elemMatch needed.
            {"tags": {"$regex": pattern, "$options": "i"}},
            {"location": {"$regex": pattern, "$options": "i"}},
            {
                "$expr": {
                    "$gt": [
                        {
                            "$size": {
                                "$filter": {
                                    "input": {"$objectToArray": {"$ifNull": ["$specs", {}]}},
                                    "as": "kv",
                                    "cond": {
                                        "$regexMatch": {"input": "$$kv.v", "regex": pattern, "options": "i"}
                                    },
                                }
                            }
                        },
                        0,
                    ]
                }
            },
        ]
    }


# FastAPI's response_model_by_alias defaults to True, which would leak
# Mongo's `_id` wire format (aria_shared models alias id -> _id for
# storage) into every JSON response. Every route below passes this
# explicitly False so responses use the Python field name `id` instead.


def require_entity(action: Action):
    """Dependency factory: fetch `{entity_id}` (404 if missing or in
    another household) and check the caller's role against its domain (403
    if disallowed), returning the raw doc for the handler to use. One
    `Depends()` replaces the fetch/404/check_permission block that used to
    be repeated at the top of every mutating handler.

    Also 404s (not 403 — consistent with the "wrong household" case above,
    which already 404s rather than 403s to avoid confirming a record's
    existence to someone who can't see it) if the entity isn't shared with
    the caller. For `delete`, `check_permission` (owner-only, see
    `aria_auth.permissions.PERMISSIONS`) already runs first, so a member is
    rejected by that regardless of sharing — sharing governs view/edit,
    role governs delete.
    """

    async def _require_entity(
        entity_id: str,
        session: SessionContext = Depends(get_current_session),
        db: AsyncIOMotorDatabase = Depends(get_db_dep),
    ) -> dict:
        doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        check_permission(session.role, doc["domain"], action)
        # .get(), not [] — an entity created before `shared_with` existed
        # has no such key stored at all; missing means "household", same
        # as the field's own Pydantic default.
        if not has_shared_access(session, doc.get("shared_with", "household"), doc["created_by"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        return doc

    return _require_entity


async def _entity_create_body(body: EntityCreate) -> EntityCreate:
    return body


async def require_entity_create_permission(
    body: EntityCreate = Depends(_entity_create_body),
    session: SessionContext = Depends(get_current_session),
) -> None:
    check_permission(session.role, body.domain, "create")


@router.get("", response_model=list[EntityBase], response_model_by_alias=False)
async def list_entities(
    domain: EntityDomain | None = Query(default=None),
    include_archived: bool = Query(default=False),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    tag: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=100, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[EntityBase]:
    query: dict = {"household_id": session.household_id}
    if domain is not None:
        query["domain"] = domain
    if not include_archived:
        query["archived_at"] = None
    if tag:
        # Exact match (anchored), unlike `q`'s substring search — this backs
        # the tag-filter dropdown, which offers whole tag values to pick
        # from, not a type-ahead.
        query["tags"] = {"$regex": f"^{re.escape(tag)}$", "$options": "i"}
    if q:
        # Own $and key (not $or) so this composes with, rather than gets
        # overwritten by, the sharing $or assigned below — Mongo ANDs all
        # top-level filter keys, so "$and: [...], $or: [...]" means (search)
        # AND (sharing), not "the last one written wins."
        query["$and"] = [_search_filter(q)]
    if session.role != "owner":
        # Owner sees everything unfiltered (has_shared_access's own
        # owner-role branch, expressed as a query instead of a per-doc
        # check). Relies on MongoDB's standard scalar-or-array equality:
        # {"shared_with": session.user_id} matches a doc where the field is
        # an array *containing* that value — exactly the membership test
        # needed, no $in/$elemMatch required. The `$exists: False` clause
        # covers entities created before `shared_with` existed at all —
        # missing means "household", same as the field's own default;
        # without it, a pre-migration entity would silently vanish from
        # every non-owner's list (caught live against real migrated data).
        query["$or"] = [
            {"shared_with": "household"},
            {"shared_with": {"$exists": False}},
            {"shared_with": session.user_id},
            {"created_by": session.user_id},
        ]
    docs = (
        await db.entities.find(query)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return [EntityBase.model_validate(doc) for doc in docs]


class TagsPage(BaseModel):
    tags: list[str]
    has_more: bool


@router.get("/tags", response_model=TagsPage, response_model_by_alias=False)
async def list_entity_tags(
    q: str | None = Query(default=None, min_length=1, max_length=200),
    domain: EntityDomain | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> TagsPage:
    """Distinct tag values across the household's entities, paginated and
    searchable — a household accumulates tags fast enough (a few hundred in
    normal use) that deriving filter options from a capped page of
    `GET /entities` results, as the frontend used to, silently hid tags
    outside that page. Declared ahead of `/{entity_id}` below so "tags"
    isn't swallowed as an entity id.
    """
    match: dict = {"household_id": session.household_id}
    if domain is not None:
        match["domain"] = domain
    if not include_archived:
        match["archived_at"] = None
    if session.role != "owner":
        match["$or"] = [
            {"shared_with": "household"},
            {"shared_with": {"$exists": False}},
            {"shared_with": session.user_id},
            {"created_by": session.user_id},
        ]

    pipeline: list[dict] = [{"$match": match}, {"$unwind": "$tags"}]
    if q:
        pipeline.append({"$match": {"tags": {"$regex": re.escape(q.strip()), "$options": "i"}}})
    pipeline += [
        {"$group": {"_id": "$tags"}},
        {"$sort": {"_id": 1}},
        {"$skip": offset},
        # Fetch one extra to detect a next page without a separate count query.
        {"$limit": limit + 1},
    ]

    docs = await db.entities.aggregate(pipeline).to_list(length=limit + 1)
    tags = [doc["_id"] for doc in docs]
    return TagsPage(tags=tags[:limit], has_more=len(tags) > limit)


@router.get("/{entity_id}", response_model=EntityBase, response_model_by_alias=False)
async def get_entity(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> EntityBase:
    doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    if not has_shared_access(session, doc.get("shared_with", "household"), doc["created_by"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    return EntityBase.model_validate(doc)


@router.post(
    "",
    response_model=EntityBase,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
    dependencies=[Depends(require_entity_create_permission)],
)
async def create_entity(
    body: EntityCreate = Depends(_entity_create_body),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> EntityBase:
    await validate_shared_with(db, session.household_id, body.shared_with)

    now = datetime.now(timezone.utc)
    try:
        entity = EntityBase(
            id=new_id(),
            household_id=session.household_id,
            domain=body.domain,
            name=body.name,
            status=body.status,
            tags=body.tags,
            location=body.location,
            specs=body.specs,
            shared_with=body.shared_with,
            created_by=session.user_id,
            created_at=now,
            updated_at=now,
            archived_at=None,
            attributes=body.attributes,
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.entities.insert_one(entity.to_mongo())
    return entity


@router.patch("/{entity_id}", response_model=EntityBase, response_model_by_alias=False)
async def update_entity(
    entity_id: str,
    body: EntityUpdate,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("update")),
) -> EntityBase:
    current = EntityBase.model_validate(doc)

    if "shared_with" in body.model_fields_set:
        # Everyone with sharing access can edit a record's content, but
        # narrowing/widening *who else* can see it is reserved for whoever
        # created it (or the household owner) — otherwise any member with
        # edit access could unilaterally revoke every other member's
        # access, including the creator's.
        if session.role != "owner" and session.user_id != current.created_by:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "only the creator or household owner may change sharing"
            )
        await validate_shared_with(db, session.household_id, body.shared_with)

    merged_data = current.model_dump()
    merged_data.update(body.model_dump(exclude_unset=True))
    merged_data["updated_at"] = datetime.now(timezone.utc)

    try:
        merged = EntityBase.model_validate(merged_data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.entities.replace_one({"_id": entity_id}, merged.to_mongo())
    return merged


@router.post("/{entity_id}/archive", response_model=EntityBase, response_model_by_alias=False)
async def archive_entity(
    entity_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("archive")),
) -> EntityBase:
    now = datetime.now(timezone.utc)
    await db.entities.update_one({"_id": entity_id}, {"$set": {"archived_at": now, "updated_at": now}})
    doc["archived_at"] = now
    doc["updated_at"] = now
    return EntityBase.model_validate(doc)


@router.post("/{entity_id}/restore", response_model=EntityBase, response_model_by_alias=False)
async def restore_entity(
    entity_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("restore")),
) -> EntityBase:
    now = datetime.now(timezone.utc)
    await db.entities.update_one({"_id": entity_id}, {"$set": {"archived_at": None, "updated_at": now}})
    doc["archived_at"] = None
    doc["updated_at"] = now
    return EntityBase.model_validate(doc)


@router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    _doc: dict = Depends(require_entity("delete")),
) -> Response:
    # Unlike schedule deletion (routers/schedules.py), which intentionally
    # leaves referencing logs' schedule_id dangling because the entity+log
    # are still viewable — deleting the entity itself removes the only page
    # its logs/schedules could ever be viewed from, so cascade rather than
    # leave unreachable orphans in Mongo.
    await db.logs.delete_many({"entity_id": entity_id, "household_id": session.household_id})
    await db.schedules.delete_many({"entity_id": entity_id, "household_id": session.household_id})

    # Documents are many-to-many with entities (a receipt can cover several
    # items), so the entity being deleted doesn't necessarily mean its
    # documents should vanish too — only unlink this entity, then clean up
    # any document that's now referenced by nothing at all. The orphan
    # check re-reads each document's state *after* the unlink instead of
    # working off the pre-update snapshot: two concurrent entity deletions
    # that both unlink the same document would otherwise each compute
    # "remaining" from their own stale snapshot and both see a non-empty
    # list, so neither enqueues cleanup even though the document ends up
    # referencing nothing.
    referencing_doc_ids = [
        doc["_id"]
        for doc in await db.documents.find(
            {"entity_ids": entity_id, "household_id": session.household_id},
            {"_id": 1},
        ).to_list(length=None)
    ]
    await db.documents.update_many(
        {"entity_ids": entity_id, "household_id": session.household_id},
        {"$pull": {"entity_ids": entity_id}},
    )
    if referencing_doc_ids:
        current_docs = await db.documents.find(
            {"_id": {"$in": referencing_doc_ids}},
            {"entity_ids": 1, "log_ids": 1, "storage_path": 1},
        ).to_list(length=None)
        for doc in current_docs:
            if not doc.get("entity_ids") and not doc.get("log_ids"):
                enqueue_document_deletion(doc["_id"], doc["storage_path"])

    await db.entities.delete_one({"_id": entity_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
