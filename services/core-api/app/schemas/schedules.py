from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    title: str
    active: bool = True
    interval_type: Literal["time", "usage"]
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None
    # Baseline seed so a brand-new schedule has a next_due_* immediately,
    # rather than staying due-less until a log completes it. Not part of
    # the canonical Schedule model — collapsed into last_completed_at /
    # last_completed_usage_value once the schedule is created.
    starting_at: date | None = None
    starting_usage_value: float | None = None

    @model_validator(mode="after")
    def _check_interval_fields(self) -> "ScheduleCreate":
        if self.interval_type == "time":
            if self.interval_days is None:
                raise ValueError("interval_days is required when interval_type is 'time'")
            if self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "usage_metric/interval_usage_amount must be unset when interval_type is 'time'"
                )
            if self.starting_usage_value is not None:
                raise ValueError("starting_usage_value must be unset when interval_type is 'time'")
        else:
            if self.usage_metric is None or self.interval_usage_amount is None:
                raise ValueError(
                    "usage_metric and interval_usage_amount are required when interval_type is 'usage'"
                )
            if self.interval_days is not None:
                raise ValueError("interval_days must be unset when interval_type is 'usage'")
            if self.starting_at is not None:
                raise ValueError("starting_at must be unset when interval_type is 'usage'")
        return self


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    active: bool | None = None
    interval_type: Literal["time", "usage"] | None = None
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None
