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
