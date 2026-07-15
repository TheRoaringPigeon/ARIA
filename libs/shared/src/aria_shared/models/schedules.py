from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

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

    @model_validator(mode="after")
    def _check_interval_fields(self) -> "Schedule":
        if self.interval_type == "time":
            if self.interval_days is None:
                raise ValueError("interval_days is required when interval_type is 'time'")
            if self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "usage_metric/interval_usage_amount must be unset when interval_type is 'time'"
                )
        else:  # "usage"
            if self.usage_metric is None or self.interval_usage_amount is None:
                raise ValueError(
                    "usage_metric and interval_usage_amount are required when interval_type is 'usage'"
                )
            if self.interval_days is not None:
                raise ValueError("interval_days must be unset when interval_type is 'usage'")
        return self
