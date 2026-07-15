from datetime import date, datetime
from typing import Literal

from pydantic import Field

from aria_shared.models.entities import EntityDomain
from aria_shared.types import MongoBaseModel, PyObjectId

LogType = Literal["service", "repair", "inspection", "expense", "note", "milestone"]


class LogEntry(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_id: PyObjectId
    domain: EntityDomain
    type: LogType
    occurred_at: date
    title: str
    description: str | None = None
    cost: float | None = None
    metrics: dict[str, str] = {}
    document_ids: list[PyObjectId] = []
    schedule_id: PyObjectId | None = None
    created_by: PyObjectId
    created_at: datetime
    updated_at: datetime
