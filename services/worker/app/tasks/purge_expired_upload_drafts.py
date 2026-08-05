from datetime import datetime, timedelta, timezone

from app import s3
from app.celery_app import celery_app
from app.config import settings
from app.db import get_db


@celery_app.task(name="app.tasks.purge_expired_upload_drafts.purge_expired_upload_drafts")
def purge_expired_upload_drafts() -> dict:
    """Hourly sweep, driven by Celery Beat (see celery_app.py). Backstop
    for mobile photo-capture drafts (document_drafts, M12) abandoned mid-
    shoot — deletes each stale draft's page S3 objects and its Mongo row.

    Keyed off `last_activity_at` (bumped on every create/page-upload/
    delete/reorder), not `created_at` — a draft resumed and actively
    edited after several days of inactivity is left alone, only genuine
    inactivity past the TTL gets purged. See
    docs/plans/m12-mobile-photo-capture.md.
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.upload_draft_ttl_hours)

    expired = list(
        db.document_drafts.find(
            {"last_activity_at": {"$lt": cutoff}}, {"_id": 1, "pages": 1}
        )
    )

    purged = 0
    for draft in expired:
        for page in draft.get("pages", []):
            s3.delete(page["storage_path"])
        db.document_drafts.delete_one({"_id": draft["_id"]})
        purged += 1

    return {"purged": purged}
