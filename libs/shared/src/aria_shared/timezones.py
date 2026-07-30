from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def _resolve(tz_name: str | None) -> ZoneInfo | timezone:
    """`None` (a household that hasn't set one) behaves as UTC."""
    return ZoneInfo(tz_name) if tz_name else timezone.utc


def household_today(tz_name: str | None) -> date:
    return datetime.now(_resolve(tz_name)).date()


def to_household_date(instant: datetime, tz_name: str | None) -> date:
    # Datetimes read back from Mongo (e.g. Schedule.created_at) are naive —
    # this codebase's convention is that a naive datetime is always UTC
    # wall-clock, never local system time. `.astimezone()` on a naive
    # datetime instead assumes the *server's* local zone, which would be
    # silently wrong here, so pin UTC first when tzinfo is absent.
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(_resolve(tz_name)).date()
