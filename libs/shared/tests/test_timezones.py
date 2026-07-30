from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aria_shared.timezones import household_today, to_household_date


def test_household_today_none_uses_utc():
    now_utc = datetime.now(timezone.utc)
    assert household_today(None) == now_utc.date()


def test_household_today_uses_named_zone():
    tz_name = "Pacific/Kiritimati"  # UTC+14 — reliably a day ahead of UTC
    expected = datetime.now(ZoneInfo(tz_name)).date()
    assert household_today(tz_name) == expected


def test_to_household_date_converts_aware_instant():
    # 2026-07-30T23:30:00Z is still 2026-07-30 in UTC but already
    # 2026-07-31 in a UTC+14 zone.
    instant = datetime(2026, 7, 30, 23, 30, tzinfo=timezone.utc)
    assert to_household_date(instant, "Pacific/Kiritimati") == date(2026, 7, 31)
    assert to_household_date(instant, None) == date(2026, 7, 30)


def test_to_household_date_treats_naive_instant_as_utc():
    # Datetimes read back from Mongo (e.g. Schedule.created_at) are naive —
    # this must be interpreted as UTC, not the host's local zone.
    naive_instant = datetime(2026, 7, 30, 23, 30)
    assert to_household_date(naive_instant, "Pacific/Kiritimati") == date(2026, 7, 31)
    assert to_household_date(naive_instant, None) == date(2026, 7, 30)
