from datetime import date

from pydantic import BaseModel, ConfigDict

from aria_shared.models.logs import LogType


class LogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    type: LogType
    occurred_at: date
    title: str
    description: str | None = None
    cost: float | None = None
    metrics: dict[str, str] = {}
    document_ids: list[str] = []
    schedule_id: str | None = None


class LogUpdate(BaseModel):
    """Partial update. entity_id/domain/schedule_id are deliberately not
    fields here — entity_id/domain are immutable once a log is created (same
    rationale as EntityUpdate), and re-linking a log to a *different*
    schedule is an edge case not worth supporting yet. Editing/deleting a
    log that's still linked to its original schedule is handled by
    routers/logs.py resyncing the schedule's cached state from whatever log
    is now the most recent for it.
    """

    model_config = ConfigDict(extra="forbid")

    type: LogType | None = None
    occurred_at: date | None = None
    title: str | None = None
    description: str | None = None
    cost: float | None = None
    metrics: dict[str, str] | None = None
    document_ids: list[str] | None = None
