from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from aria_shared.models.entities import EntityDomain
from aria_shared.types import MongoBaseModel, PyObjectId

LogType = Literal[
    "service",
    "repair",
    "inspection",
    "expense",
    "note",
    "milestone",
    "conversation",
    "call",
    "meeting",
    "gift",
]

# Per-domain type vocab, mirroring STATUS_BY_DOMAIN in entities.py — the
# maintenance-flavored types never made sense for people, and the
# person-flavored types (conversation/call/meeting/gift) don't make sense
# for a vehicle, so this is enforced rather than left as a free-for-all.
LOG_TYPES_BY_DOMAIN: dict[EntityDomain, set[LogType]] = {
    "home": {"service", "repair", "inspection", "expense", "note", "milestone"},
    "vehicle": {"service", "repair", "inspection", "expense", "note", "milestone"},
    "equipment": {"service", "repair", "inspection", "expense", "note", "milestone"},
    "project": {"service", "repair", "inspection", "expense", "note", "milestone"},
    "person": {"conversation", "call", "meeting", "gift", "milestone"},
}


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

    @model_validator(mode="after")
    def _check_type_valid_for_domain(self) -> "LogEntry":
        valid_types = LOG_TYPES_BY_DOMAIN[self.domain]
        if self.type not in valid_types:
            raise ValueError(
                f"type {self.type!r} is not valid for domain {self.domain!r}; "
                f"expected one of {sorted(valid_types)}"
            )
        return self
