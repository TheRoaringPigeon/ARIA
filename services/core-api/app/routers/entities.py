from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app.dependencies import SessionContext, get_current_session, get_db_dep
from app.ids import new_id
from app.schemas.entities import EntityCreate, EntityUpdate
from aria_auth import Action, check_permission
from aria_shared.models import EntityBase, EntityDomain

router = APIRouter(prefix="/entities", tags=["entities"])

MAX_LIMIT = 200

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
    docs = (
        await db.entities.find(query)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return [EntityBase.model_validate(doc) for doc in docs]


@router.get("/{entity_id}", response_model=EntityBase, response_model_by_alias=False)
async def get_entity(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> EntityBase:
    doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
    if doc is None:
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
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("update")),
) -> EntityBase:
    current = EntityBase.model_validate(doc)
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
    await db.entities.delete_one({"_id": entity_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
