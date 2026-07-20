from datetime import datetime, timezone

from app import chroma, ollama, s3
from app.celery_app import celery_app
from app.db import get_db
from app.logic.chunking import chunk_pages
from app.logic.ocr import extract_pages


def _set_status(db, document_id: str, status: str, **extra) -> None:
    db.documents.update_one(
        {"_id": document_id},
        {"$set": {"processing_status": status, "updated_at": datetime.now(timezone.utc), **extra}},
    )


@celery_app.task(name="app.tasks.process_document.process_document")
def process_document(document_id: str) -> None:
    """OCR -> chunk -> embed -> write to Chroma, one stage after another,
    updating `Document.processing_status` as each completes so a client
    polling mid-run sees real progress. Any failure anywhere in the
    pipeline is caught once at the top level: the document is marked
    `failed` (with the error recorded) rather than retried, and stays
    fully visible/downloadable via core-api's CRUD endpoints — only the
    semantic-search path is missing.
    """
    db = get_db()
    doc = db.documents.find_one({"_id": document_id})
    if doc is None:
        return

    try:
        file_bytes = s3.download(doc["storage_path"])

        pages = extract_pages(file_bytes, doc["mime_type"])
        _set_status(db, document_id, "ocr_complete", page_count=len(pages))

        chunks = chunk_pages(pages)
        _set_status(db, document_id, "chunked")

        if chunks:
            embeddings = ollama.embed_batch([chunk.text for chunk in chunks])
            metadatas = []
            for chunk in chunks:
                metadata = {
                    "mongo_document_id": document_id,
                    "household_id": doc["household_id"],
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                }
                if chunk.section_header is not None:
                    metadata["section_header"] = chunk.section_header
                metadatas.append(metadata)

            # The entity-delete cascade can enqueue delete_document for this
            # same id while this task is still mid-pipeline; re-check the
            # row hasn't been removed underneath us right before writing to
            # Chroma so a losing race doesn't leave orphaned vectors behind
            # for a document that no longer exists in Mongo/S3.
            if db.documents.find_one({"_id": document_id}) is None:
                return

            chroma.get_documents_collection().add(
                ids=[f"{document_id}:{chunk.chunk_index}" for chunk in chunks],
                embeddings=embeddings,
                documents=[chunk.text for chunk in chunks],
                metadatas=metadatas,
            )
        _set_status(db, document_id, "embedded")
    except Exception as exc:
        _set_status(db, document_id, "failed", processing_error=str(exc))
