from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.config import settings
from app.db import get_db, start_transaction
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

    Batched across the whole expired set, not looped per-entity — a fixed
    number of round trips (~8) regardless of how many entities are expired
    in a given run, instead of the 6+ round trips per entity a naive loop
    would cost (see CLAUDE.md's "Mongo write patterns" for the general
    shape of this fix). entity_id is globally unique (app/ids.py's
    new_id(), not namespaced by household), and logs/schedules/documents/
    users.pinned_entity_ids all reference it directly — so every step below
    can use a single household_id-agnostic $in filter across every
    household sharing this hourly Beat run, not just within one.

    The entities/logs/schedules/documents/users writes all commit or roll
    back together in one transaction (app/db.py's start_transaction()) —
    without this, a crash between deleting from `entities` and finishing the
    rest of the cascade would permanently orphan that batch's logs/
    schedules/document-links/pins, since the next run's expired_filter query
    can never find an already-deleted entity again to retry it. This also
    covers two overlapping runs (a slow run still going when Beat's next
    hourly fire lands, or a manual re-trigger): Mongo detects the write
    conflict between their two `entities.delete_many` calls on the same
    documents and aborts the losing transaction outright, so a duplicate
    cascade (including a duplicate delete_document.delay() enqueue) is never
    committed — the losing run just fails and the next hourly sweep picks up
    whatever's still actually expired.
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.entity_trash_grace_hours)

    expired_filter = {"pending_delete_at": {"$ne": None, "$lt": cutoff}}
    expired_ids = [e["_id"] for e in db.entities.find(expired_filter, {"_id": 1})]
    if not expired_ids:
        return {"purged": 0}

    orphaned_docs = []
    with start_transaction() as txn:
        # Delete conditioned on the same still-expired filter used to find
        # the candidates, re-applied here rather than trusting the snapshot
        # above — a household member can restore-from-trash in between,
        # which clears pending_delete_at and makes that document stop
        # matching. Mongo evaluates this per-document, so it's the same
        # race-safety guarantee the old per-entity delete_one had, just
        # applied to the whole batch in one round trip.
        db.entities.delete_many({"_id": {"$in": expired_ids}, **expired_filter}, session=txn)

        # delete_many only returns a count, not which ids matched — diff the
        # candidate snapshot against what's still present now to recover
        # exactly which entities were actually purged (survivors were
        # restored in the interim and must be excluded from the rest of the
        # cascade, same as the old per-entity skip).
        still_present = {
            d["_id"] for d in db.entities.find({"_id": {"$in": expired_ids}}, {"_id": 1}, session=txn)
        }
        purged_ids = [eid for eid in expired_ids if eid not in still_present]

        db.logs.delete_many({"entity_id": {"$in": purged_ids}}, session=txn)
        db.schedules.delete_many({"entity_id": {"$in": purged_ids}}, session=txn)

        # Same orphan-check shape as the old inline cascade: re-read each
        # referencing document's state after the unlink, not before, so a
        # document still linked to another (non-purged) entity is left
        # alone. delete_document.delay() itself happens after the
        # transaction below, not here — enqueuing is a Celery broker call,
        # not a Mongo write, so it can't be part of the rollback and must
        # only fire once the cascade is confirmed committed.
        referencing_doc_ids = [
            doc["_id"]
            for doc in db.documents.find({"entity_ids": {"$in": purged_ids}}, {"_id": 1}, session=txn)
        ]
        db.documents.update_many(
            {"entity_ids": {"$in": purged_ids}},
            {"$pull": {"entity_ids": {"$in": purged_ids}}},
            session=txn,
        )
        if referencing_doc_ids:
            current_docs = db.documents.find(
                {"_id": {"$in": referencing_doc_ids}},
                {"entity_ids": 1, "log_ids": 1, "storage_path": 1},
                session=txn,
            )
            orphaned_docs = [
                doc for doc in current_docs if not doc.get("entity_ids") and not doc.get("log_ids")
            ]

        db.users.update_many(
            {"pinned_entity_ids": {"$in": purged_ids}},
            {"$pull": {"pinned_entity_ids": {"$in": purged_ids}}},
            session=txn,
        )

    for doc in orphaned_docs:
        delete_document.delay(doc["_id"], doc["storage_path"])

    return {"purged": len(purged_ids)}
