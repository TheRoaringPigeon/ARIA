from datetime import date, datetime
from typing import Literal

from pydantic import Field

from aria_shared.models.entities import EntityDomain
from aria_shared.types import MongoBaseModel, PyObjectId


class Schedule(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_id: PyObjectId
    domain: EntityDomain
    title: str
    active: bool = True

    interval_type: Literal["time", "usage"]
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None

    last_completed_log_id: PyObjectId | None = None
    last_completed_at: date | None = None
    last_completed_usage_value: float | None = None

    next_due_at: date | None = None
    next_due_usage_value: float | None = None

    created_by: PyObjectId
    created_at: datetime
    updated_at: datetime
