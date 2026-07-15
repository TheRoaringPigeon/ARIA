from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app.dependencies import SessionContext, get_current_session, get_db_dep
from app.ids import new_id
from app.logic.schedules import ScheduleBaseline, compute_next_due
from app.schemas.logs import LogCreate
from aria_shared.models import LogEntry, Schedule

router = APIRouter(tags=["logs"])


@router.post(
    "/logs",
    response_model=LogEntry,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_log(
    body: LogCreate,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> LogEntry:
    entity_doc = await db.entities.find_one(
        {"_id": body.entity_id, "household_id": session.household_id}
    )
    if entity_doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")

    # PATCH/DELETE on logs are intentionally out of scope for M1 — the exit
    # criteria only needs create, and editing/deleting a schedule-linked log
    # has no reliable rollback path (schedules carry incremental state,
    # nothing scans historical logs — data-model.md §5).
    current_schedule: Schedule | None = None
    usage_value: float | None = None
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
        current_schedule = Schedule.model_validate(schedule_doc)

        if current_schedule.interval_type == "usage":
            raw_value = body.metrics.get(current_schedule.usage_metric)
            if raw_value is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"metrics[{current_schedule.usage_metric!r}] is required to "
                    "complete this usage-based schedule",
                )
            try:
                usage_value = float(raw_value)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"metrics[{current_schedule.usage_metric!r}] must be a number, "
                    f"got {raw_value!r}",
                ) from exc

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

    if current_schedule is not None:
        baseline = ScheduleBaseline(
            interval_type=current_schedule.interval_type,
            interval_days=current_schedule.interval_days,
            interval_usage_amount=current_schedule.interval_usage_amount,
            last_completed_at=body.occurred_at if current_schedule.interval_type == "time" else None,
            last_completed_usage_value=usage_value,
        )
        next_due = compute_next_due(baseline)

        updated_schedule = current_schedule.model_copy(
            update={
                "last_completed_log_id": log.id,
                "last_completed_at": baseline.last_completed_at,
                "last_completed_usage_value": baseline.last_completed_usage_value,
                "next_due_at": next_due.next_due_at,
                "next_due_usage_value": next_due.next_due_usage_value,
                "updated_at": now,
            }
        )
        await db.schedules.replace_one({"_id": current_schedule.id}, updated_schedule.to_mongo())

    return log


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
