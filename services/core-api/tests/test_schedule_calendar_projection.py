from datetime import date

from app.logic.schedules import ScheduleBaseline, project_occurrences


def _baseline(**overrides) -> ScheduleBaseline:
    defaults = dict(
        interval_type="time",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=None,
        last_completed_usage_value=None,
    )
    defaults.update(overrides)
    return ScheduleBaseline(**defaults)


def test_once_in_range():
    baseline = _baseline(interval_type="once", planned_at=date(2026, 7, 20))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == [date(2026, 7, 20)]


def test_once_out_of_range():
    baseline = _baseline(interval_type="once", planned_at=date(2026, 8, 1))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == []


def test_once_before_created_at_excluded():
    # planned_at predates when the schedule was created — shouldn't happen in
    # practice (creation seeds planned_at itself) but the floor still applies.
    baseline = _baseline(interval_type="once", planned_at=date(2026, 1, 1))
    result = project_occurrences(baseline, created_at=date(2026, 6, 1), range_from=date(2026, 1, 1), range_to=date(2026, 12, 31))
    assert result == []


def test_once_shows_even_after_completion():
    # A completed "once" schedule still has planned_at set (compute_next_due
    # clears next_due_at, not planned_at) — the calendar's job is "what's on
    # this day," not "what's still outstanding."
    baseline = _baseline(interval_type="once", planned_at=date(2026, 7, 20), last_completed_at=date(2026, 7, 20))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == [date(2026, 7, 20)]


def test_time_forward_projection():
    baseline = _baseline(interval_type="time", interval_days=7, last_completed_at=date(2026, 7, 1))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == [date(2026, 7, 8), date(2026, 7, 15), date(2026, 7, 22), date(2026, 7, 29)]


def test_time_backward_projection_across_month_boundary():
    # Anchor is in August; asking for July should project backward correctly.
    baseline = _baseline(interval_type="time", interval_days=7, last_completed_at=date(2026, 8, 5))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15), date(2026, 7, 22), date(2026, 7, 29)]


def test_time_excludes_anchor_itself():
    baseline = _baseline(interval_type="time", interval_days=7, last_completed_at=date(2026, 7, 15))
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert date(2026, 7, 15) not in result
    assert result == [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 22), date(2026, 7, 29)]


def test_time_range_entirely_before_created_at_is_empty():
    baseline = _baseline(interval_type="time", interval_days=7, last_completed_at=date(2026, 7, 1))
    result = project_occurrences(baseline, created_at=date(2026, 9, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == []


def test_time_no_baseline_is_empty():
    baseline = _baseline(interval_type="time", interval_days=7, last_completed_at=None)
    result = project_occurrences(baseline, created_at=date(2026, 1, 1), range_from=date(2026, 7, 1), range_to=date(2026, 7, 31))
    assert result == []


def test_monthly_day_across_month_boundary():
    baseline = _baseline(interval_type="monthly", monthly_day=4, last_completed_at=date(2020, 1, 1))
    result = project_occurrences(baseline, created_at=date(2020, 1, 1), range_from=date(2026, 6, 20), range_to=date(2026, 7, 20))
    assert result == [date(2026, 7, 4)]


def test_monthly_day_31_clamps_in_february():
    baseline = _baseline(interval_type="monthly", monthly_day=31, last_completed_at=date(2020, 1, 1))
    result = project_occurrences(baseline, created_at=date(2020, 1, 1), range_from=date(2026, 2, 1), range_to=date(2026, 2, 28))
    assert result == [date(2026, 2, 28)]


def test_monthly_nth_weekday_range():
    # 2nd Friday of each month, June-July 2026: June 12, July 10.
    baseline = _baseline(
        interval_type="monthly", monthly_weekday=4, monthly_week_index=2, last_completed_at=date(2020, 1, 1)
    )
    result = project_occurrences(baseline, created_at=date(2020, 1, 1), range_from=date(2026, 6, 1), range_to=date(2026, 7, 31))
    assert result == [date(2026, 6, 12), date(2026, 7, 10)]


def test_monthly_range_entirely_before_created_at_is_empty():
    baseline = _baseline(interval_type="monthly", monthly_day=4, last_completed_at=date(2020, 1, 1))
    result = project_occurrences(baseline, created_at=date(2026, 8, 1), range_from=date(2026, 6, 1), range_to=date(2026, 7, 31))
    assert result == []


def test_once_on_created_at_date_included():
    # `created_at` is expected to already be the household-local calendar
    # date (aria_shared.timezones.to_household_date) by the time it reaches
    # here — an occurrence exactly on that date is in range with no slack.
    baseline = _baseline(interval_type="once", planned_at=date(2026, 6, 1))
    result = project_occurrences(
        baseline, created_at=date(2026, 6, 1), range_from=date(2026, 1, 1), range_to=date(2026, 12, 31)
    )
    assert result == [date(2026, 6, 1)]


def test_once_day_before_created_at_date_excluded():
    # Previously this would have landed inside a one-day floor slack — a
    # workaround for `project_occurrences` receiving a raw UTC date with no
    # household timezone to convert it correctly. Now that callers pass an
    # already-converted local date, there's no slack: a real day before
    # creation is excluded, not forgiven.
    baseline = _baseline(interval_type="once", planned_at=date(2026, 5, 31))
    result = project_occurrences(
        baseline, created_at=date(2026, 6, 1), range_from=date(2026, 1, 1), range_to=date(2026, 12, 31)
    )
    assert result == []
