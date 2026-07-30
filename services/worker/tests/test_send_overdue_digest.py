import importlib
from datetime import date, datetime, timezone

import pytest

# `app/tasks/__init__.py` does `from app.tasks.send_overdue_digest import
# send_overdue_digest`, which — because the imported name is identical to
# the submodule name — overwrites `app.tasks`'s `send_overdue_digest`
# attribute with the function itself once `app.tasks` finishes importing.
# Both `from app.tasks import send_overdue_digest` and
# `import app.tasks.send_overdue_digest as x` resolve through that same
# clobbered attribute, so neither gets the module. `importlib` sidesteps
# it by reading straight out of `sys.modules`.
digest_task = importlib.import_module("app.tasks.send_overdue_digest")


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in" and actual not in operand:
                    return False
                if op == "$ne" and actual == operand:
                    return False
                if op == "$lt" and not (actual is not None and actual < operand):
                    return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, field, direction=1):
        self._docs = sorted(self._docs, key=lambda d: d[field], reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query=None):
        query = query or {}
        return FakeCursor([d for d in self._docs if _matches(d, query)])

    def find_one(self, query, projection=None):
        for d in self._docs:
            if _matches(d, query):
                return d
        return None


class FakeDb:
    def __init__(self, *, users, households, schedules, entities):
        self.users = FakeCollection(users)
        self.households = FakeCollection(households)
        self.schedules = FakeCollection(schedules)
        self.entities = FakeCollection(entities)


@pytest.fixture(autouse=True)
def capture_sent_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(digest_task.mail, "send_mail", lambda **kwargs: sent.append(kwargs))
    return sent


def _entity(entity_id, name):
    return {"_id": entity_id, "name": name, "created_by": "owner1", "shared_with": "household"}


def _user(user_id, household_id):
    return {
        "_id": user_id,
        "household_id": household_id,
        "email": f"{user_id}@example.com",
        "role": "owner",
        "notify_overdue_email": True,
    }


def _schedule(schedule_id, household_id, entity_id, title, next_due_at):
    return {
        "_id": schedule_id,
        "household_id": household_id,
        "entity_id": entity_id,
        "active": True,
        "pending_delete_at": None,
        "interval_type": "time",
        "title": title,
        "next_due_at": next_due_at,
    }


def test_overdue_cutoff_is_computed_per_household_timezone(monkeypatch, capture_sent_mail):
    """The regression this backlog item fixes: `send_overdue_digest` used
    to compare every household's schedules against one global UTC-midnight
    cutoff. Two households with the same `next_due_at` should now land on
    opposite sides of "overdue" once their stored timezones disagree about
    what day it is — proving the per-household lookup (households.timezone)
    actually reaches the cutoff calculation, not just that `household_today`
    itself is correct (that's covered separately in
    libs/shared/tests/test_timezones.py). `household_today` is faked here
    rather than exercised for real, so the test doesn't depend on wall-clock
    time or real zoneinfo data — only on this module calling it with each
    household's own stored timezone.
    """
    fake_today_by_tz = {"tz-ahead": date(2026, 7, 31), "tz-behind": date(2026, 7, 29)}
    monkeypatch.setattr(digest_task, "household_today", lambda tz: fake_today_by_tz[tz])

    same_next_due_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    db = FakeDb(
        users=[_user("owner1", "h1"), _user("owner2", "h2")],
        households=[{"_id": "h1", "timezone": "tz-ahead"}, {"_id": "h2", "timezone": "tz-behind"}],
        schedules=[
            _schedule("s1", "h1", "e1", "Flush tank", same_next_due_at),
            _schedule("s2", "h2", "e2", "Rotate tires", same_next_due_at),
        ],
        entities=[_entity("e1", "Water heater"), _entity("e2", "Car")],
    )
    monkeypatch.setattr(digest_task, "get_db", lambda: db)

    result = digest_task.send_overdue_digest()

    # h1's "today" (7/31) is after next_due_at (7/30) -> overdue.
    # h2's "today" (7/29) is before next_due_at (7/30) -> not yet due.
    assert result == {"households_notified": 1, "emails_sent": 1}
    assert len(capture_sent_mail) == 1
    assert capture_sent_mail[0]["to"] == "owner1@example.com"
    assert "Flush tank" in capture_sent_mail[0]["body_text"]


def test_household_with_no_timezone_set_falls_back_to_utc(monkeypatch, capture_sent_mail):
    monkeypatch.setattr(digest_task, "household_today", lambda tz: date(2026, 7, 31) if tz is None else date(1970, 1, 1))

    db = FakeDb(
        users=[_user("owner1", "h1")],
        households=[{"_id": "h1"}],  # no "timezone" key at all, same as an unmigrated doc
        schedules=[_schedule("s1", "h1", "e1", "Flush tank", datetime(2026, 7, 30, tzinfo=timezone.utc))],
        entities=[_entity("e1", "Water heater")],
    )
    monkeypatch.setattr(digest_task, "get_db", lambda: db)

    result = digest_task.send_overdue_digest()

    assert result == {"households_notified": 1, "emails_sent": 1}
