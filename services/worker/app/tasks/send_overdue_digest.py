import logging
from datetime import datetime, timezone

from app import mail
from app.celery_app import celery_app
from app.config import settings
from app.db import get_db

logger = logging.getLogger(__name__)


def _render_digest(items: list[dict]) -> tuple[str, str]:
    subject = f"ARIA: {len(items)} overdue item{'s' if len(items) != 1 else ''}"
    lines = [f"You have {len(items)} overdue item(s) in ARIA:", ""]
    for item in items:
        lines.append(f"- {item['title']} ({item['entity_name']}) — was due {item['next_due_at']}")
    lines += ["", f"Open ARIA: {settings.frontend_origin}/due-soon"]
    return subject, "\n".join(lines)


def _visible_to(user: dict, entity: dict | None) -> bool:
    """Mirrors has_shared_access (aria_auth.sharing) without a live
    SessionContext: owner and the entity's own creator always see it,
    otherwise only if shared_with is "household" or names this user.
    """
    if entity is None:
        return False
    if user.get("role") == "owner":
        return True
    if user["_id"] == entity.get("created_by"):
        return True
    shared_with = entity.get("shared_with", "household")
    if shared_with == "household":
        return True
    return user["_id"] in shared_with


@celery_app.task(name="app.tasks.send_overdue_digest.send_overdue_digest")
def send_overdue_digest() -> dict:
    """Daily digest, driven by Celery Beat (see celery_app.py). Date-based
    schedules only, same restriction as GET /schedules/due-soon
    (usage-based due tracking has no reliable current-reading source yet).
    Sends one email per opted-in user, scoped to their own household's
    overdue items and filtered per-user by entity sharing (_visible_to
    above, mirroring _visible_schedules in
    core-api/app/routers/schedules.py) so a member never sees an entity's
    schedule in email that the app itself would hide from them.
    """
    db = get_db()
    today_midnight = datetime.combine(
        datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc
    )

    users_by_household: dict = {}
    for user in db.users.find({"notify_overdue_email": True}):
        users_by_household.setdefault(user["household_id"], []).append(user)

    households_notified = 0
    emails_sent = 0

    for household_id, users in users_by_household.items():
        overdue_docs = list(
            db.schedules.find(
                {
                    "household_id": household_id,
                    "active": True,
                    "interval_type": {"$in": ["time", "once", "monthly"]},
                    "next_due_at": {"$ne": None, "$lt": today_midnight},
                }
            ).sort("next_due_at", 1)
        )
        if not overdue_docs:
            continue

        entity_ids = list({d["entity_id"] for d in overdue_docs})
        entity_by_id = {e["_id"]: e for e in db.entities.find({"_id": {"$in": entity_ids}})}

        household_notified = False
        for user in users:
            visible_docs = [d for d in overdue_docs if _visible_to(user, entity_by_id.get(d["entity_id"]))]
            if not visible_docs:
                continue

            items = [
                {
                    "title": d["title"],
                    "entity_name": entity_by_id.get(d["entity_id"], {}).get("name", "(unknown entity)"),
                    "next_due_at": d["next_due_at"].date().isoformat(),
                }
                for d in visible_docs
            ]
            subject, body = _render_digest(items)
            try:
                mail.send_mail(to=user["email"], subject=subject, body_text=body)
                emails_sent += 1
                household_notified = True
            except Exception:
                logger.exception("failed to send overdue digest to %s", user["email"])

        if household_notified:
            households_notified += 1

    return {"households_notified": households_notified, "emails_sent": emails_sent}
