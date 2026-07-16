from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ValidationError

from app.dependencies import (
    SessionContext,
    get_current_session,
    get_db_dep,
    require_entity_for_create,
)
from app.ids import new_id
from app.logic.schedules import NextDue, ScheduleBaseline, compute_next_due
from app.schemas.schedules import ScheduleCreate, ScheduleUpdate
from aria_auth import Action, check_permission
from aria_shared.models import Schedule

router = APIRouter(tags=["schedules"])


def require_schedule(action: Action):
    """Dependency factory: fetch `{schedule_id}` (404 if missing or in
    another household) and check the caller's role against its domain (403
    if disallowed), returning the raw doc for the handler to use.
    """

    async def _require_schedule(
        schedule_id: str,
        session: SessionContext = Depends(get_current_session),
        db: AsyncIOMotorDatabase = Depends(get_db_dep),
    ) -> dict:
        doc = await db.schedules.find_one({"_id": schedule_id, "household_id": session.household_id})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")
        check_permission(session.role, doc["domain"], action)
        return doc

    return _require_schedule


async def _schedule_create_body(body: ScheduleCreate) -> ScheduleCreate:
    return body


require_entity_for_schedule_create = require_entity_for_create(_schedule_create_body)


def _baseline_from_schedule_fields(data: dict) -> ScheduleBaseline:
    return ScheduleBaseline(
        interval_type=data["interval_type"],
        interval_days=data.get("interval_days"),
        interval_usage_amount=data.get("interval_usage_amount"),
        last_completed_at=data.get("last_completed_at"),
        last_completed_usage_value=data.get("last_completed_usage_value"),
        planned_at=data.get("planned_at"),
        monthly_day=data.get("monthly_day"),
        monthly_weekday=data.get("monthly_weekday"),
        monthly_week_index=data.get("monthly_week_index"),
    )


@router.post(
    "/schedules",
    response_model=Schedule,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_schedule(
    body: ScheduleCreate = Depends(_schedule_create_body),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    entity_doc: dict = Depends(require_entity_for_schedule_create),
) -> Schedule:
    now = datetime.now(timezone.utc)

    # Seed a baseline so a brand-new schedule has a next_due_* immediately,
    # rather than staying due-less until a log completes it (see
    # app/schemas/schedules.py). The seed is stored as last_completed_at /
    # last_completed_usage_value with last_completed_log_id left unset —
    # there's no separate "starting point" field on the canonical Schedule
    # model; the first real completion (a POST /logs with schedule_id set)
    # overwrites it the same way any subsequent completion would.
    if body.interval_type == "time":
        baseline_at = body.starting_at if body.starting_at is not None else date.today()
        baseline = ScheduleBaseline(
            interval_type="time",
            interval_days=body.interval_days,
            interval_usage_amount=None,
            last_completed_at=baseline_at,
            last_completed_usage_value=None,
        )
    elif body.interval_type == "usage":
        baseline = ScheduleBaseline(
            interval_type="usage",
            interval_days=None,
            interval_usage_amount=body.interval_usage_amount,
            last_completed_at=None,
            last_completed_usage_value=body.starting_usage_value,
        )
    elif body.interval_type == "once":
        baseline = ScheduleBaseline(
            interval_type="once",
            interval_days=None,
            interval_usage_amount=None,
            last_completed_at=None,
            last_completed_usage_value=None,
            planned_at=body.planned_at,
        )
    else:  # "monthly"
        baseline_at = body.starting_at if body.starting_at is not None else date.today()
        baseline = ScheduleBaseline(
            interval_type="monthly",
            interval_days=None,
            interval_usage_amount=None,
            last_completed_at=baseline_at,
            last_completed_usage_value=None,
            monthly_day=body.monthly_day,
            monthly_weekday=body.monthly_weekday,
            monthly_week_index=body.monthly_week_index,
        )

    next_due = compute_next_due(baseline)

    try:
        schedule = Schedule(
            id=new_id(),
            household_id=session.household_id,
            entity_id=body.entity_id,
            domain=entity_doc["domain"],
            title=body.title,
            active=body.active,
            interval_type=body.interval_type,
            interval_days=body.interval_days,
            usage_metric=body.usage_metric,
            interval_usage_amount=body.interval_usage_amount,
            planned_at=body.planned_at,
            planned_time=body.planned_time,
            monthly_day=body.monthly_day,
            monthly_weekday=body.monthly_weekday,
            monthly_week_index=body.monthly_week_index,
            last_completed_log_id=None,
            last_completed_at=baseline.last_completed_at,
            last_completed_usage_value=baseline.last_completed_usage_value,
            next_due_at=next_due.next_due_at,
            next_due_usage_value=next_due.next_due_usage_value,
            created_by=session.user_id,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.schedules.insert_one(schedule.to_mongo())
    return schedule


@router.patch("/schedules/{schedule_id}", response_model=Schedule, response_model_by_alias=False)
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_schedule("update")),
) -> Schedule:
    current = Schedule.model_validate(doc)
    merged_data = current.model_dump()
    merged_data.update(body.model_dump(exclude_unset=True))
    merged_data["updated_at"] = datetime.now(timezone.utc)

    # Interval fields may have changed — recompute next_due_* from the
    # existing baseline so the cached due values never go stale relative to
    # the (possibly new) rule, per data-model.md §5's explicit call-out.
    next_due: NextDue = compute_next_due(_baseline_from_schedule_fields(merged_data))
    merged_data["next_due_at"] = next_due.next_due_at
    merged_data["next_due_usage_value"] = next_due.next_due_usage_value

    try:
        merged = Schedule.model_validate(merged_data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.schedules.replace_one({"_id": schedule_id}, merged.to_mongo())
    return merged


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    _doc: dict = Depends(require_schedule("delete")),
) -> Response:
    # Logs that reference this schedule_id keep it as-is rather than being
    # unlinked — same reasoning as archival elsewhere in this app: a deleted
    # schedule shouldn't retroactively rewrite history. A dangling
    # schedule_id is harmless: _resync_schedule already no-ops when the
    # schedule it's asked to resync no longer exists, and nothing in the
    # frontend dereferences a log's schedule_id for display.
    await db.schedules.delete_one({"_id": schedule_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/entities/{entity_id}/schedules",
    response_model=list[Schedule],
    response_model_by_alias=False,
)
async def list_entity_schedules(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[Schedule]:
    entity_doc = await db.entities.find_one(
        {"_id": entity_id, "household_id": session.household_id}
    )
    if entity_doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")

    docs = await db.schedules.find(
        {"entity_id": entity_id, "household_id": session.household_id}
    ).to_list(length=None)
    return [Schedule.model_validate(doc) for doc in docs]


class DueScheduleItem(BaseModel):
    schedule: Schedule
    entity_name: str
    is_overdue: bool


@router.get(
    "/schedules/due-soon", response_model=list[DueScheduleItem], response_model_by_alias=False
)
async def list_due_soon(
    within_days: int = Query(default=30, ge=0, le=3650),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[DueScheduleItem]:
    """Date-based schedules only — usage-based due tracking needs a reliable
    "current reading" source that doesn't exist yet (the free-text
    logs.metrics[usage_metric] key isn't guaranteed to line up with e.g.
    VehicleAttrs.current_mileage). Deferred, not faked. "time"/"once"/
    "monthly" all share the same next_due_at-is-a-real-date shape, so one
    query covers "oil change due next week", "coffee with Sandra next
    week", and "rent due the 1st" together.
    """
    today = date.today()
    horizon = today + timedelta(days=within_days)

    docs = (
        await db.schedules.find(
            {
                "household_id": session.household_id,
                "active": True,
                "interval_type": {"$in": ["time", "once", "monthly"]},
                "next_due_at": {"$ne": None, "$lte": datetime.combine(horizon, datetime.min.time(), tzinfo=timezone.utc)},
            }
        )
        .sort("next_due_at", 1)
        .to_list(length=None)
    )
    if not docs:
        return []

    schedules = [Schedule.model_validate(doc) for doc in docs]
    entity_ids = list({s.entity_id for s in schedules})
    entity_docs = await db.entities.find({"_id": {"$in": entity_ids}}).to_list(length=None)
    entity_names = {doc["_id"]: doc["name"] for doc in entity_docs}

    return [
        DueScheduleItem(
            schedule=s,
            entity_name=entity_names.get(s.entity_id, "(unknown entity)"),
            is_overdue=s.next_due_at is not None and s.next_due_at < today,
        )
        for s in schedules
    ]
