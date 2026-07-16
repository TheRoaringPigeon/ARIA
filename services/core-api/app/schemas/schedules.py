from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    title: str
    active: bool = True
    interval_type: Literal["time", "usage", "once", "monthly"]
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None
    # Baseline seed so a brand-new schedule has a next_due_* immediately,
    # rather than staying due-less until a log completes it. Not part of
    # the canonical Schedule model — collapsed into last_completed_at /
    # last_completed_usage_value once the schedule is created. Also used to
    # seed "monthly" (same reasoning as "time": both recur off a
    # last_completed_at baseline).
    starting_at: date | None = None
    starting_usage_value: float | None = None
    # "once" only — required, and unlike starting_at this *is* a permanent
    # field on the canonical Schedule model (see aria_shared/models/schedules.py).
    planned_at: date | None = None
    # Optional time-of-day, any interval_type — see Schedule.planned_time.
    planned_time: str | None = None
    # "monthly" only — see Schedule.monthly_day/monthly_weekday/monthly_week_index.
    monthly_day: int | None = None
    monthly_weekday: int | None = None
    monthly_week_index: int | None = None

    @model_validator(mode="after")
    def _check_interval_fields(self) -> "ScheduleCreate":
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
            if self.starting_usage_value is not None:
                raise ValueError("starting_usage_value must be unset when interval_type is 'time'")
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
            if self.starting_at is not None:
                raise ValueError("starting_at must be unset when interval_type is 'usage'")
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
            if self.starting_at is not None or self.starting_usage_value is not None:
                raise ValueError("starting_at/starting_usage_value must be unset when interval_type is 'once'")
            if monthly_fields_set:
                raise ValueError("monthly_* fields must be unset when interval_type is 'once'")
        else:  # "monthly"
            if self.interval_days is not None or self.usage_metric is not None or self.interval_usage_amount is not None:
                raise ValueError(
                    "interval_days/usage_metric/interval_usage_amount must be unset when interval_type is 'monthly'"
                )
            if self.planned_at is not None or self.starting_usage_value is not None:
                raise ValueError("planned_at/starting_usage_value must be unset when interval_type is 'monthly'")
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


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    active: bool | None = None
    interval_type: Literal["time", "usage", "once", "monthly"] | None = None
    interval_days: int | None = None
    usage_metric: str | None = None
    interval_usage_amount: float | None = None
    planned_at: date | None = None
    planned_time: str | None = None
    monthly_day: int | None = None
    monthly_weekday: int | None = None
    monthly_week_index: int | None = None
    # Same meaning as ScheduleCreate.starting_at/starting_usage_value: not a
    # field on the canonical Schedule model, just a seed. Setting one here
    # re-seeds the schedule's baseline (last_completed_at/
    # last_completed_usage_value) — used both to move an existing "time"/
    # "monthly" schedule's anchor date (or a "usage" schedule's current
    # reading) without switching interval_type, and to seed the new type
    # when interval_type *is* changing. See routers/schedules.py
    # update_schedule for how these two cases are told apart.
    starting_at: date | None = None
    starting_usage_value: float | None = None
