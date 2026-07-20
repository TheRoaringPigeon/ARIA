from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from aria_shared.models.logs import LogType


class LogCreate(BaseModel):
    """A completed maintenance item, expense, note, or other household event
    against a tracked entity (home, vehicle, equipment, project, or person).

    Single source of truth for this request shape: core-api's `POST /logs`
    validates against it directly, and ai-service's `create_log` MCP tool
    uses it as its tool-argument model, so a field added/removed/redescribed
    here takes effect in both places without either needing a matching edit.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(description="id of the entity this log is against")
    type: LogType = Field(
        description=(
            "one of: service, repair, inspection, expense, note, milestone, "
            "conversation, call, meeting, gift — must be valid for the "
            "target entity's domain"
        )
    )
    occurred_at: date = Field(description="ISO date (YYYY-MM-DD) the event occurred")
    title: str
    description: str | None = None
    cost: float | None = None
    metrics: dict[str, str] = {}
    document_ids: list[str] = []
    schedule_id: str | None = Field(
        default=None, description="schedule this log completes, if any"
    )


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
