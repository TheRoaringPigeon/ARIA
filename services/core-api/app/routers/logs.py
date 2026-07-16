from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app.dependencies import (
    SessionContext,
    get_current_session,
    get_db_dep,
    require_entity_for_create,
)
from app.ids import new_id
from app.logic.schedules import ScheduleBaseline, compute_next_due
from app.schemas.logs import LogCreate, LogUpdate
from aria_auth import Action, check_permission
from aria_shared.models import LogEntry, Schedule

router = APIRouter(tags=["logs"])


def require_log(action: Action):
    """Dependency factory: fetch `{log_id}` (404 if missing or in another
    household) and check the caller's role against its domain (403 if
    disallowed), returning the raw doc for the handler to use.
    """

    async def _require_log(
        log_id: str,
        session: SessionContext = Depends(get_current_session),
        db: AsyncIOMotorDatabase = Depends(get_db_dep),
    ) -> dict:
        doc = await db.logs.find_one({"_id": log_id, "household_id": session.household_id})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "log not found")
        check_permission(session.role, doc["domain"], action)
        return doc

    return _require_log


async def _log_create_body(body: LogCreate) -> LogCreate:
    return body


require_entity_for_log_create = require_entity_for_create(_log_create_body)


def _require_usage_value(schedule: Schedule, metrics: dict[str, str]) -> None:
    """Fail fast, before any write, if a usage-based schedule is being
    completed without the metric it tracks — same check on create and
    update so a PATCH can't silently detach a log's metrics from the
    schedule it's supposed to be satisfying.
    """
    if schedule.interval_type != "usage":
        return
    raw_value = metrics.get(schedule.usage_metric)
    if raw_value is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"metrics[{schedule.usage_metric!r}] is required to complete this usage-based schedule",
        )
    try:
        float(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"metrics[{schedule.usage_metric!r}] must be a number, got {raw_value!r}",
        ) from exc


async def _resync_schedule(db: AsyncIOMotorDatabase, schedule_id: str, household_id: str) -> None:
    """Recompute a schedule's cached last_completed_*/next_due_* from
    whichever log is now genuinely the most recent one linked to it.

    Runs after any log create/update/delete that touches a schedule_id,
    rather than only ever incrementally advancing forward on create — that
    incremental-only version blindly trusted whatever log was written most
    recently, so logging an old entry out of order (or editing/deleting the
    log that set the current baseline) would leave the cache wrong with no
    way to fix it. Requerying "most recent by occurred_at for this
    schedule_id" is still a single bounded query (not a household-wide
    scan), so it doesn't violate the data-model.md §5 no-scan intent.

    One real gap: if this was the *only* log ever linked to the schedule,
    there's nothing left to derive a baseline from — the schedule's
    original creation-time seed (`starting_at`/`starting_usage_value`) was
    already overwritten at first-completion and isn't recoverable. In that
    case this resets last_completed_*/next_due_* to None (schedule reverts
    to "not yet due-trackable") rather than leaving stale values behind.
    """
    schedule_doc = await db.schedules.find_one({"_id": schedule_id, "household_id": household_id})
    if schedule_doc is None:
        return
    schedule = Schedule.model_validate(schedule_doc)

    latest_doc = await db.logs.find_one(
        {"schedule_id": schedule_id, "household_id": household_id},
        sort=[("occurred_at", -1)],
    )

    last_completed_log_id: str | None = None
    last_completed_at = None
    last_completed_usage_value: float | None = None
    if latest_doc is not None:
        latest_log = LogEntry.model_validate(latest_doc)
        last_completed_log_id = latest_log.id
        if schedule.interval_type in ("time", "once", "monthly"):
            last_completed_at = latest_log.occurred_at
        else:
            raw_value = latest_log.metrics.get(schedule.usage_metric)
            try:
                last_completed_usage_value = float(raw_value) if raw_value is not None else None
            except ValueError:
                last_completed_usage_value = None

    next_due = compute_next_due(
        ScheduleBaseline(
            interval_type=schedule.interval_type,
            interval_days=schedule.interval_days,
            interval_usage_amount=schedule.interval_usage_amount,
            last_completed_at=last_completed_at,
            last_completed_usage_value=last_completed_usage_value,
            planned_at=schedule.planned_at,
            monthly_day=schedule.monthly_day,
            monthly_weekday=schedule.monthly_weekday,
            monthly_week_index=schedule.monthly_week_index,
        )
    )

    updated_schedule = schedule.model_copy(
        update={
            "last_completed_log_id": last_completed_log_id,
            "last_completed_at": last_completed_at,
            "last_completed_usage_value": last_completed_usage_value,
            "next_due_at": next_due.next_due_at,
            "next_due_usage_value": next_due.next_due_usage_value,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    # model_copy + to_mongo(), not a raw $set — to_mongo() is what converts
    # bare `date` fields (last_completed_at) into BSON-safe UTC-midnight
    # `datetime`s (aria_shared/types.py); a raw $set with a `date` value
    # raises InvalidDocument.
    await db.schedules.replace_one({"_id": schedule_id}, updated_schedule.to_mongo())


@router.post(
    "/logs",
    response_model=LogEntry,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_log(
    body: LogCreate = Depends(_log_create_body),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    entity_doc: dict = Depends(require_entity_for_log_create),
) -> LogEntry:
    if body.schedule_id is not None:
        schedule_doc = await db.schedules.find_one(
            {
                "_id": body.schedule_id,
                "household_id": session.household_id,
                "entity_id": body.entity_id,
            }
        )
        if schedule_doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found for this entity")
        _require_usage_value(Schedule.model_validate(schedule_doc), body.metrics)

    now = datetime.now(timezone.utc)
    try:
        log = LogEntry(
            id=new_id(),
            household_id=session.household_id,
            entity_id=body.entity_id,
            domain=entity_doc["domain"],
            type=body.type,
            occurred_at=body.occurred_at,
            title=body.title,
            description=body.description,
            cost=body.cost,
            metrics=body.metrics,
            document_ids=body.document_ids,
            schedule_id=body.schedule_id,
            created_by=session.user_id,
            created_at=now,
            updated_at=now,
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Validate everything above *before* any write. No Mongo transaction
    # (compose's mongo:7 isn't a replica set) — worst case on a
    # mid-sequence failure between these two writes is a stale schedule
    # cache, which is recoverable and explicitly acceptable per
    # data-model.md: "logging without a schedule link is still fully valid."
    await db.logs.insert_one(log.to_mongo())

    if body.schedule_id is not None:
        await _resync_schedule(db, body.schedule_id, session.household_id)

    return log


@router.patch("/logs/{log_id}", response_model=LogEntry, response_model_by_alias=False)
async def update_log(
    log_id: str,
    body: LogUpdate,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_log("update")),
) -> LogEntry:
    current = LogEntry.model_validate(doc)
    merged_data = current.model_dump()
    merged_data.update(body.model_dump(exclude_unset=True))
    merged_data["updated_at"] = datetime.now(timezone.utc)

    try:
        merged = LogEntry.model_validate(merged_data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if merged.schedule_id is not None:
        schedule_doc = await db.schedules.find_one(
            {"_id": merged.schedule_id, "household_id": session.household_id}
        )
        if schedule_doc is not None:
            _require_usage_value(Schedule.model_validate(schedule_doc), merged.metrics)

    await db.logs.replace_one({"_id": log_id}, merged.to_mongo())

    if merged.schedule_id is not None:
        await _resync_schedule(db, merged.schedule_id, session.household_id)

    return merged


@router.delete("/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    log_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_log("delete")),
) -> Response:
    current = LogEntry.model_validate(doc)
    await db.logs.delete_one({"_id": log_id})

    if current.schedule_id is not None:
        await _resync_schedule(db, current.schedule_id, session.household_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/entities/{entity_id}/logs", response_model=list[LogEntry], response_model_by_alias=False
)
async def list_entity_logs(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[LogEntry]:
    entity_doc = await db.entities.find_one(
        {"_id": entity_id, "household_id": session.household_id}
    )
    if entity_doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")

    docs = (
        await db.logs.find({"entity_id": entity_id, "household_id": session.household_id})
        .sort("occurred_at", -1)
        .to_list(length=None)
    )
    return [LogEntry.model_validate(doc) for doc in docs]
