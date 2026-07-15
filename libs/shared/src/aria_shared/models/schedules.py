import re
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from aria_shared.models.entities import EntityDomain
from aria_shared.types import MongoBaseModel, PyObjectId

_TIME_OF_DAY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Schedule(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_id: PyObjectId
    domain: EntityDomain
    title: str
    active: bool = True

    interval_type: Literal["time", "usage", "once", "monthly"]
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None
    # Only meaningful for interval_type == "once" — the target date for a
    # single planned occurrence (e.g. "coffee with Sandra on the 20th").
    # Unlike interval_days/usage_metric, this is a permanent field rather
    # than a create-time seed collapsed into last_completed_at: a "once"
    # schedule has no recurrence to fall back on, so if the completing log
    # is later edited/deleted, resyncing needs the original planned date to
    # still be there rather than lost (see routers/logs.py _resync_schedule).
    planned_at: date | None = None
    # "monthly" only — either monthly_day (day-of-month, e.g. "the 4th") or
    # monthly_weekday + monthly_week_index (e.g. "2nd Friday"/"last Friday"),
    # mutually exclusive. Permanent fields, same reasoning as planned_at:
    # the rule itself, not a one-time seed.
    monthly_day: int | None = None
    monthly_weekday: int | None = None  # 0=Monday..6=Sunday
    monthly_week_index: int | None = None  # 1-4, or -1 for "last"
    # Purely informational time-of-day, e.g. "19:00" — doesn't feed into
    # next_due_at/due-soon comparisons (those stay day-granularity, same as
    # the rest of this app's scheduling model), just carried alongside
    # planned_at/next_due_at for display. Not restricted to any particular
    # interval_type or domain, but the only UI that sets it today is the
    # person "Plans" frontend feature.
    planned_time: str | None = None

    last_completed_log_id: PyObjectId | None = None
    last_completed_at: date | None = None
    last_completed_usage_value: float | None = None

    next_due_at: date | None = None
    next_due_usage_value: float | None = None

    created_by: PyObjectId
    created_at: datetime
    updated_at: datetime

    @field_validator("planned_time")
    @classmethod
    def _check_planned_time_format(cls, value: str | None) -> str | None:
        if value is not None and not _TIME_OF_DAY_RE.match(value):
            raise ValueError(f"planned_time must be 24-hour HH:MM, got {value!r}")
        return value

    @model_validator(mode="after")
    def _check_interval_fields(self) -> "Schedule":
        monthly_fields_set = (
            self.monthly_day is not None
            or self.monthly_weekday is not None
            or self.monthly_week_index is not None
        )
        if self.interval_type == "time":
            if self.interval_days is None:
                raise ValueError("interval_days is required when interval_type is 'time'")
            if self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "usage_metric/interval_usage_amount must be unset when interval_type is 'time'"
                )
            if self.planned_at is not None:
                raise ValueError("planned_at must be unset when interval_type is 'time'")
            if monthly_fields_set:
                raise ValueError("monthly_* fields must be unset when interval_type is 'time'")
        elif self.interval_type == "usage":
            if self.usage_metric is None or self.interval_usage_amount is None:
                raise ValueError(
                    "usage_metric and interval_usage_amount are required when interval_type is 'usage'"
                )
            if self.interval_days is not None:
                raise ValueError("interval_days must be unset when interval_type is 'usage'")
            if self.planned_at is not None:
                raise ValueError("planned_at must be unset when interval_type is 'usage'")
            if monthly_fields_set:
                raise ValueError("monthly_* fields must be unset when interval_type is 'usage'")
        elif self.interval_type == "once":
            if self.planned_at is None:
                raise ValueError("planned_at is required when interval_type is 'once'")
            if self.interval_days is not None or self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "interval_days/usage_metric/interval_usage_amount must be unset when interval_type is 'once'"
                )
            if monthly_fields_set:
                raise ValueError("monthly_* fields must be unset when interval_type is 'once'")
        else:  # "monthly"
            if self.interval_days is not None or self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "interval_days/usage_metric/interval_usage_amount must be unset when interval_type is 'monthly'"
                )
            if self.planned_at is not None:
                raise ValueError("planned_at must be unset when interval_type is 'monthly'")
            day_mode = self.monthly_day is not None
            weekday_mode = self.monthly_weekday is not None or self.monthly_week_index is not None
            if day_mode and weekday_mode:
                raise ValueError("monthly_day and monthly_weekday/monthly_week_index are mutually exclusive")
            if not day_mode and not weekday_mode:
                raise ValueError(
                    "monthly requires either monthly_day, or monthly_weekday + monthly_week_index"
                )
            if day_mode:
                if not (1 <= self.monthly_day <= 31):
                    raise ValueError("monthly_day must be between 1 and 31")
            else:
                if self.monthly_weekday is None or self.monthly_week_index is None:
                    raise ValueError("monthly_weekday and monthly_week_index must both be set together")
                if not (0 <= self.monthly_weekday <= 6):
                    raise ValueError("monthly_weekday must be 0 (Monday) through 6 (Sunday)")
                if self.monthly_week_index not in (1, 2, 3, 4, -1):
                    raise ValueError("monthly_week_index must be 1-4, or -1 for 'last'")
        return self
