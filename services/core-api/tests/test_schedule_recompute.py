from datetime import date

from app.logic.schedules import ScheduleBaseline, compute_next_due


def test_time_based_recompute():
    baseline = ScheduleBaseline(
        interval_type="time",
        interval_days=90,
        interval_usage_amount=None,
        last_completed_at=date(2026, 1, 1),
        last_completed_usage_value=None,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2026, 4, 1)
    assert result.next_due_usage_value is None


def test_usage_based_recompute():
    baseline = ScheduleBaseline(
        interval_type="usage",
        interval_days=None,
        interval_usage_amount=5000,
        last_completed_at=None,
        last_completed_usage_value=42000,
    )
    result = compute_next_due(baseline)
    assert result.next_due_usage_value == 47000
    assert result.next_due_at is None


def test_time_based_no_baseline_returns_none():
    baseline = ScheduleBaseline(
        interval_type="time",
        interval_days=90,
        interval_usage_amount=None,
        last_completed_at=None,
        last_completed_usage_value=None,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at is None
    assert result.next_due_usage_value is None


def test_usage_based_no_baseline_returns_none():
    baseline = ScheduleBaseline(
        interval_type="usage",
        interval_days=None,
        interval_usage_amount=5000,
        last_completed_at=None,
        last_completed_usage_value=None,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at is None
    assert result.next_due_usage_value is None


def test_once_pending_is_due_on_planned_date():
    baseline = ScheduleBaseline(
        interval_type="once",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=None,
        last_completed_usage_value=None,
        planned_at=date(2026, 7, 20),
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2026, 7, 20)
    assert result.next_due_usage_value is None


def test_once_completed_has_nothing_further_due():
    baseline = ScheduleBaseline(
        interval_type="once",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2026, 7, 20),
        last_completed_usage_value=None,
        planned_at=date(2026, 7, 20),
    )
    result = compute_next_due(baseline)
    assert result.next_due_at is None
    assert result.next_due_usage_value is None


def test_monthly_day_of_month_recompute():
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2026, 7, 15),
        last_completed_usage_value=None,
        monthly_day=4,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2026, 8, 4)  # the 4th already passed this month
    assert result.next_due_usage_value is None


def test_monthly_day_of_month_same_month_if_still_ahead():
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2026, 7, 1),
        last_completed_usage_value=None,
        monthly_day=4,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2026, 7, 4)


def test_monthly_day_of_month_clamps_to_month_end():
    # 2026 is not a leap year — Feb has 28 days, so "the 31st" clamps to Feb 28.
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2026, 1, 31),
        last_completed_usage_value=None,
        monthly_day=31,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2026, 2, 28)


def test_monthly_nth_weekday_recompute():
    # Jan 1 2024 is a Monday. 2nd Friday of Jan 2024 is Jan 12.
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2023, 12, 15),
        last_completed_usage_value=None,
        monthly_weekday=4,  # Friday
        monthly_week_index=2,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2024, 1, 12)


def test_monthly_last_weekday_recompute():
    # Dec 15 2023 baseline: the last Friday of December itself (Dec 29) is
    # still ahead of the baseline, so it's due this month, not skipped to
    # January's last Friday (Jan 26) — unlike the 2nd-Friday case above,
    # where December's 2nd Friday (Dec 8) had already passed.
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=date(2023, 12, 15),
        last_completed_usage_value=None,
        monthly_weekday=4,  # Friday
        monthly_week_index=-1,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at == date(2023, 12, 29)


def test_monthly_no_baseline_returns_none():
    baseline = ScheduleBaseline(
        interval_type="monthly",
        interval_days=None,
        interval_usage_amount=None,
        last_completed_at=None,
        last_completed_usage_value=None,
        monthly_day=4,
    )
    result = compute_next_due(baseline)
    assert result.next_due_at is None
    assert result.next_due_usage_value is None
