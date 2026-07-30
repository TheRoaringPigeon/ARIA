from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.config import settings
from app.db import get_db
from app.tasks.delete_document import delete_document


@celery_app.task(name="app.tasks.purge_expired_trash.purge_expired_trash")
def purge_expired_trash() -> dict:
    """Hourly sweep, driven by Celery Beat (see celery_app.py). Finds
    entities trashed via DELETE /entities/{id} (core-api) whose grace
    period (settings.entity_trash_grace_hours) has elapsed, and performs
    the cascade-purge delete_entity used to do inline before trash existed
    (services/core-api/app/routers/entities.py): delete the entity itself
    (conditionally — see below), then its logs/schedules for real, unlink
    it from any document, enqueue cleanup for documents left orphaned, and
    drop the entity id from every household member's pinned_entity_ids.
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.entity_trash_grace_hours)

    expired = list(
        db.entities.find({"pending_delete_at": {"$ne": None, "$lt": cutoff}}, {"_id": 1, "household_id": 1})
    )
    purged = 0

    for entity in expired:
        entity_id = entity["_id"]
        household_id = entity["household_id"]

        # Delete the entity first, conditioned on the same still-expired
        # filter used to find it, rather than trusting the snapshot above —
        # a household member can restore-from-trash between the find() and
        # here (this loop does several sequential round trips per entity),
        # which clears pending_delete_at and makes this a no-match. Skip the
        # rest of the cascade entirely when that happens, instead of
        # deleting logs/schedules/documents out from under an entity the
        # user just explicitly brought back.
        result = db.entities.delete_one(
            {"_id": entity_id, "pending_delete_at": {"$ne": None, "$lt": cutoff}}
        )
        if result.deleted_count == 0:
            continue

        db.logs.delete_many({"entity_id": entity_id, "household_id": household_id})
        db.schedules.delete_many({"entity_id": entity_id, "household_id": household_id})

        # Same orphan-check shape as the old inline cascade: re-read each
        # referencing document's state after the unlink, not before, so a
        # document still linked to another (non-trashed) entity is left
        # alone.
        referencing_doc_ids = [
            doc["_id"]
            for doc in db.documents.find(
                {"entity_ids": entity_id, "household_id": household_id}, {"_id": 1}
            )
        ]
        db.documents.update_many(
            {"entity_ids": entity_id, "household_id": household_id},
            {"$pull": {"entity_ids": entity_id}},
        )
        if referencing_doc_ids:
            current_docs = db.documents.find(
                {"_id": {"$in": referencing_doc_ids}},
                {"entity_ids": 1, "log_ids": 1, "storage_path": 1},
            )
            for doc in current_docs:
                if not doc.get("entity_ids") and not doc.get("log_ids"):
                    delete_document.delay(doc["_id"], doc["storage_path"])

        db.users.update_many(
            {"household_id": household_id},
            {"$pull": {"pinned_entity_ids": entity_id}},
        )

        purged += 1

    return {"purged": purged}
