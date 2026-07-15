from fastapi import APIRouter, Query

from app.db import get_db
from aria_shared.models import EntityBase

router = APIRouter(prefix="/entities", tags=["entities"])

MAX_LIMIT = 200


@router.get("", response_model=list[EntityBase])
async def list_entities(
    household_id: str,
    limit: int = Query(default=100, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[EntityBase]:
    """Scaffolding-stage endpoint: proves the FastAPI -> Motor -> Mongo path
    works end to end. Auth-derived household scoping lands in a follow-up
    pass; for now the caller passes household_id explicitly.
    """
    db = get_db()
    docs = (
        await db.entities.find({"household_id": household_id})
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return [EntityBase.model_validate(doc) for doc in docs]
