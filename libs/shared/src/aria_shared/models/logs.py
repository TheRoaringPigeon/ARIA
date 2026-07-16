from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from aria_shared.models.entities import ENTITY_DOMAINS, EntityDomain
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
        valid_types = ENTITY_DOMAINS[self.domain].LOG_TYPES
        if self.type not in valid_types:
            raise ValueError(
                f"type {self.type!r} is not valid for domain {self.domain!r}; "
                f"expected one of {sorted(valid_types)}"
            )
        return self
