from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal


@dataclass
class ScheduleBaseline:
    """The inputs compute_next_due needs — deliberately a plain dataclass,
    not the Mongo-backed Schedule model, so this stays a pure function with
    no import of aria_shared/Motor and is trivial to unit test.
    """

    interval_type: Literal["time", "usage"]
    interval_days: int | None
    interval_usage_amount: float | None
    last_completed_at: date | None
    last_completed_usage_value: float | None


@dataclass
class NextDue:
    next_due_at: date | None
    next_due_usage_value: float | None


def compute_next_due(baseline: ScheduleBaseline) -> NextDue:
    """data-model.md §5: next_due_* is cached, not computed on the fly, and
    recomputed incrementally off the schedule's own last_completed_* state —
    nothing scans historical logs. Returns (None, None) when there's no
    baseline yet to compute from (e.g. a usage-based schedule created
    without a starting usage value) — the schedule is real but not
    yet "due-trackable" until a first completion establishes the baseline.
    """
    if baseline.interval_type == "time":
        if baseline.last_completed_at is None or baseline.interval_days is None:
            return NextDue(next_due_at=None, next_due_usage_value=None)
        return NextDue(
            next_due_at=baseline.last_completed_at + timedelta(days=baseline.interval_days),
            next_due_usage_value=None,
        )

    # interval_type == "usage"
    if baseline.last_completed_usage_value is None or baseline.interval_usage_amount is None:
        return NextDue(next_due_at=None, next_due_usage_value=None)
    return NextDue(
        next_due_at=None,
        next_due_usage_value=baseline.last_completed_usage_value + baseline.interval_usage_amount,
    )
