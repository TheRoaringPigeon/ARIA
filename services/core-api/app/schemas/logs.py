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
