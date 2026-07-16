from app import chroma, s3
from app.celery_app import celery_app
from app.db import get_db


@celery_app.task(name="app.tasks.delete_document.delete_document")
def delete_document(document_id: str, storage_path: str) -> None:
    """Delete a document from every store that might hold a trace of it.

    Takes `storage_path` as an argument rather than looking it up via
    `document_id` so the task is self-contained even when the caller (e.g.
    the direct-delete endpoint) already removed the Mongo row before
    enqueuing — every step here is a no-op on an already-missing target, so
    the task is safe to run whether the Mongo row still exists (the entity-
    cascade orphan case) or not.
    """
    chroma.get_documents_collection().delete(where={"mongo_document_id": document_id})
    s3.delete(storage_path)
    get_db().documents.delete_one({"_id": document_id})
