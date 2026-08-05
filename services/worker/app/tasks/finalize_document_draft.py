from datetime import datetime, timezone
from io import BytesIO

from bson import ObjectId
from PIL import Image

from app import s3
from app.celery_app import celery_app
from app.config import settings
from app.db import get_db
from app.tasks.process_document import process_document
from aria_shared.models import Document

_RESULT_FILENAME = "mobile-scan.pdf"


def _new_id() -> str:
    return str(ObjectId())


def _visible_to(user: dict, entity: dict) -> bool:
    """Mirrors aria_auth.sharing.has_shared_access without a live
    SessionContext — same pattern as send_overdue_digest.py's _visible_to.
    Worker stays free of aria_auth's fastapi/motor dependencies; see that
    module for the precedent.
    """
    if user.get("role") == "owner":
        return True
    if user["_id"] == entity.get("created_by"):
        return True
    shared_with = entity.get("shared_with", "household")
    if shared_with == "household":
        return True
    return user["_id"] in shared_with


def _entities_still_accessible(db, draft: dict) -> bool:
    """Sync re-check of core-api's _check_entity_access
    (services/core-api/app/routers/documents.py), re-run here because a
    linked entity can be archived, unshared, or trashed-and-purged by
    another household member during the time a draft sits in `capturing` —
    this is the last point before a Document is actually created.

    `create` is currently unrestricted by role for every domain in
    aria_auth.permissions.PERMISSIONS (only delete/undelete are gated) —
    if that table ever adds a per-domain `create` restriction, this needs
    a matching role check added here.
    """
    user = db.users.find_one({"_id": draft["created_by"]})
    if user is None:
        return False
    for entity_id in draft["entity_ids"]:
        entity = db.entities.find_one({"_id": entity_id, "household_id": draft["household_id"]})
        if entity is None:
            return False
        if entity.get("archived_at") is not None:
            return False
        if entity.get("pending_delete_at") is not None:
            return False
        if not _visible_to(user, entity):
            return False
    return True


def _fail(db, draft_id: str, error: str) -> None:
    db.document_drafts.update_one(
        {"_id": draft_id},
        {"$set": {"status": "failed", "finalize_error": error}},
    )


@celery_app.task(name="app.tasks.finalize_document_draft.finalize_document_draft")
def finalize_document_draft(draft_id: str) -> None:
    """Assemble a draft's captured photos into one multi-page PDF and turn
    it into a real Document. Runs in the worker (not inline in the core-api
    request) because it downloads and re-encodes several full-resolution
    phone photos — real work, not something to do on the request thread or
    over a mobile network connection. Draft and pages are left in place on
    failure so the client can offer Retry (re-enters this task from
    `failed`) or Cancel. See docs/plans/m12-mobile-photo-capture.md.
    """
    db = get_db()
    draft = db.document_drafts.find_one({"_id": draft_id})
    if draft is None or draft["status"] != "finalizing":
        return

    if not _entities_still_accessible(db, draft):
        _fail(db, draft_id, "entity no longer accessible")
        return

    try:
        images = [
            Image.open(BytesIO(s3.download(page["storage_path"]))).convert("RGB")
            for page in draft["pages"]
        ]

        buf = BytesIO()
        images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
        pdf_bytes = buf.getvalue()

        if len(pdf_bytes) > settings.max_upload_bytes:
            _fail(
                db,
                draft_id,
                f"combined PDF exceeds maximum upload size of {settings.max_upload_bytes} bytes",
            )
            return

        document_id = _new_id()
        storage_path = f"{draft['household_id']}/{document_id}/{_RESULT_FILENAME}"
        s3.upload(storage_path, BytesIO(pdf_bytes), "application/pdf")

        document = Document(
            id=document_id,
            household_id=draft["household_id"],
            entity_ids=draft["entity_ids"],
            log_ids=[],
            document_type=draft["document_type"],
            original_filename=_RESULT_FILENAME,
            storage_path=storage_path,
            mime_type="application/pdf",
            file_size_bytes=len(pdf_bytes),
            page_count=None,
            processing_status="pending",
            processing_error=None,
            shared_with=draft["shared_with"],
            uploaded_by=draft["created_by"],
            uploaded_at=datetime.now(timezone.utc),
        )
        db.documents.insert_one(document.to_mongo())
        process_document.delay(document_id)

        for page in draft["pages"]:
            s3.delete(page["storage_path"])

        db.document_drafts.update_one(
            {"_id": draft_id},
            {
                "$set": {
                    "status": "finalized",
                    "resulting_document_id": document_id,
                    # The page S3 objects are gone as of the loop above —
                    # clear the array too so the draft row is genuinely
                    # page-less, not just missing files it still claims to
                    # have (a stale-metadata trap for anything that reads
                    # this draft after finalize).
                    "pages": [],
                }
            },
        )
    except Exception as exc:
        _fail(db, draft_id, str(exc))
