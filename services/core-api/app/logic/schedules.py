import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class ScheduleBaseline:
    """The inputs compute_next_due needs — deliberately a plain dataclass,
    not the Mongo-backed Schedule model, so this stays a pure function with
    no import of aria_shared/Motor and is trivial to unit test.
    """

    interval_type: Literal["time", "usage", "once", "monthly"]
    interval_days: int | None
    interval_usage_amount: float | None
    last_completed_at: date | None
    last_completed_usage_value: float | None
    planned_at: date | None = None
    # "monthly" only — either monthly_day (day-of-month, e.g. "the 4th") or
    # monthly_weekday + monthly_week_index (e.g. "2nd Friday"), never both.
    monthly_day: int | None = None
    monthly_weekday: int | None = None  # 0=Monday..6=Sunday
    monthly_week_index: int | None = None  # 1-4, or -1 for "last"


@dataclass
class NextDue:
    next_due_at: date | None
    next_due_usage_value: float | None


def _nth_weekday_of_month(year: int, month: int, weekday: int, week_index: int) -> date:
    """The date of the `week_index`-th `weekday` in `year`/`month`.

    `weekday`: 0=Monday..6=Sunday (matches date.weekday()). `week_index`:
    1-4 for 1st..4th, -1 for "last". Every month has at least four of each
    weekday, so 1-4 never overflows into a nonexistent occurrence — only
    "last" needs to count backward from month-end.
    """
    days_in_month = calendar.monthrange(year, month)[1]
    if week_index == -1:
        last_of_month = date(year, month, days_in_month)
        offset = (last_of_month.weekday() - weekday) % 7
        return last_of_month - timedelta(days=offset)

    first_of_month = date(year, month, 1)
    offset = (weekday - first_of_month.weekday()) % 7
    return date(year, month, 1 + offset + (week_index - 1) * 7)


def _monthly_candidate(
    year: int,
    month: int,
    monthly_day: int | None,
    monthly_weekday: int | None,
    monthly_week_index: int | None,
) -> date:
    """The monthly rule's occurrence in a given `year`/`month`.

    `monthly_day` beyond the target month's length clamps to that month's
    last day (e.g. "the 31st" lands on Feb 28/29) rather than skipping the
    month — a deliberate choice so the rule never silently goes quiet.
    """
    if monthly_day is not None:
        day = min(monthly_day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    return _nth_weekday_of_month(year, month, monthly_weekday, monthly_week_index)


def _next_monthly_occurrence(
    after: date,
    monthly_day: int | None,
    monthly_weekday: int | None,
    monthly_week_index: int | None,
) -> date:
    """The next date strictly after `after` matching the monthly rule."""
    year, month = after.year, after.month
    while True:
        candidate = _monthly_candidate(year, month, monthly_day, monthly_weekday, monthly_week_index)
        if candidate > after:
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1


def project_occurrences(
    baseline: ScheduleBaseline,
    created_at: date,
    range_from: date,
    range_to: date,
) -> list[date]:
    """All calendar-day occurrences of `baseline`'s rule within
    [range_from, range_to], bounded below by `created_at` — a schedule has
    no occurrences before it existed, which is what makes a past range
    that predates creation correctly return [] instead of phantom hits.

    This is a pure projection, not a history lookup: `next_due_at` is the
    only due-date state this app persists (data-model.md §5), so "past
    occurrences" of a `time`-type schedule are reconstructed by stepping
    backward from the *current* last_completed_at anchor, not read from a
    log of what was actually due on past dates. If the schedule is ever
    completed early or late, previously-projected past dates will shift
    the next time this is called — by design, not a bug, since there is
    no per-occurrence completion history to be faithful to instead.
    """
    # `created_at` must already be the household-local calendar date (see
    # aria_shared.timezones.to_household_date) — no slack needed here since
    # the caller has done the real timezone conversion, not this function.
    floor = max(range_from, created_at)
    if floor > range_to:
        return []

    if baseline.interval_type == "once":
        if baseline.planned_at is None:
            return []
        return [baseline.planned_at] if floor <= baseline.planned_at <= range_to else []

    if baseline.interval_type == "time":
        if baseline.last_completed_at is None or baseline.interval_days is None:
            return []
        anchor = baseline.last_completed_at
        step = baseline.interval_days
        if step <= 0:
            return []
        # (anchor - floor).days is negative when floor > anchor; Python's
        # floor division on negative ints rounds toward -inf, which is
        # exactly "smallest k such that anchor + k*step >= floor" here.
        k_min = -((anchor - floor).days // step)
        k_max = (range_to - anchor).days // step
        occurrences = []
        for k in range(k_min, k_max + 1):
            if k == 0:
                continue
            occurrence = anchor + timedelta(days=k * step)
            if floor <= occurrence <= range_to:
                occurrences.append(occurrence)
        return occurrences

    if baseline.interval_type == "monthly":
        occurrences = []
        year, month = floor.year, floor.month
        while (year, month) <= (range_to.year, range_to.month):
            candidate = _monthly_candidate(
                year, month, baseline.monthly_day, baseline.monthly_weekday, baseline.monthly_week_index
            )
            if floor <= candidate <= range_to:
                occurrences.append(candidate)
            month += 1
            if month > 12:
                month = 1
                year += 1
        return occurrences

    # "usage" — excluded by callers (no reliable current-reading source).
    # Returning [] rather than raising keeps one bad schedule from 500ing
    # the whole household's calendar, but that also makes a future caller
    # mistake (e.g. a widened interval_type filter) indistinguishable from
    # "no occurrences" — log it so the regression is at least visible.
    logger.warning("project_occurrences called with unsupported interval_type='usage'")
    return []


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

    if baseline.interval_type == "once":
        # No recurrence: due on the planned date until a log completes it,
        # then nothing further is due — ever. Unlike "time", completion
        # doesn't advance next_due_at forward by an interval, it just clears it.
        if baseline.last_completed_at is not None:
            return NextDue(next_due_at=None, next_due_usage_value=None)
        return NextDue(next_due_at=baseline.planned_at, next_due_usage_value=None)

    if baseline.interval_type == "monthly":
        if baseline.last_completed_at is None:
            return NextDue(next_due_at=None, next_due_usage_value=None)
        next_at = _next_monthly_occurrence(
            baseline.last_completed_at,
            baseline.monthly_day,
            baseline.monthly_weekday,
            baseline.monthly_week_index,
        )
        return NextDue(next_due_at=next_at, next_due_usage_value=None)

    # interval_type == "usage"
    if baseline.last_completed_usage_value is None or baseline.interval_usage_amount is None:
        return NextDue(next_due_at=None, next_due_usage_value=None)
    return NextDue(
        next_due_at=None,
        next_due_usage_value=baseline.last_completed_usage_value + baseline.interval_usage_amount,
    )
