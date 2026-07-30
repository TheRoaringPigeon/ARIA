from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery("aria_worker", broker=settings.broker_url, backend=settings.result_backend)
celery_app.autodiscover_tasks(["app"])

celery_app.conf.timezone = "UTC"

if settings.overdue_digest_interval_seconds is not None:
    # Dev-only override (see config.py) — fires every N seconds instead of
    # the daily schedule so digests are always visibly repeatable in
    # Mailpit, no code edits needed.
    _overdue_digest_schedule = float(settings.overdue_digest_interval_seconds)
else:
    # Fixed UTC time, chosen to land at 7am EST (UTC-5) — no household
    # timezone concept exists yet (see docs/qol-backlog.md's
    # household-settings item), and no DST adjustment (EST, not ET).
    _overdue_digest_schedule = crontab(hour=12, minute=0)

celery_app.conf.beat_schedule = {
    "send-overdue-digest-daily": {
        "task": "app.tasks.send_overdue_digest.send_overdue_digest",
        "schedule": _overdue_digest_schedule,
    },
    # Hourly is coarser than the grace window itself, which is fine — this
    # is a sweep, not a precise timer; an entity purges up to ~an hour after
    # its grace period technically lapses.
    "purge-expired-trash-hourly": {
        "task": "app.tasks.purge_expired_trash.purge_expired_trash",
        "schedule": crontab(minute=0),
    },
}
