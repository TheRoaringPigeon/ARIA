import logging
from datetime import datetime, timezone
from io import BytesIO

from app import chroma, ollama, s3
from app.celery_app import celery_app
from app.config import settings
from app.db import get_db
from app.logic.chunking import chunk_pages
from app.logic.ocr import extract_pages

logger = logging.getLogger(__name__)


def _set_status(db, document_id: str, status: str, **extra) -> None:
    db.documents.update_one(
        {"_id": document_id},
        {"$set": {"processing_status": status, "updated_at": datetime.now(timezone.utc), **extra}},
    )


def _write_searchable_pdf(db, document_id: str, storage_path: str, searchable_pdf: bytes) -> None:
    """Best-effort rewrite of a mobile-scan document's PDF in place with an
    OCR text layer. Runs after OCR/chunk/embed already succeeded, so any
    failure here (oversize, a delete race, anything else) must not regress
    that pipeline — it's caught and logged, leaving the document at
    whatever status chunk/embed already reached and the original
    (non-searchable) PDF in place.
    """
    try:
        if len(searchable_pdf) > settings.max_upload_bytes:
            raise ValueError(
                f"searchable PDF exceeds maximum upload size of {settings.max_upload_bytes} bytes"
            )
        # The entity-delete cascade or a direct DELETE /documents/{id} can
        # remove this row (and enqueue an S3 delete) while this task was
        # mid-OCR — same race the Chroma write below already guards
        # against. Skip the upload rather than resurrecting an S3 object
        # with no Mongo row pointing at it; nothing else purges orphaned
        # S3 keys, so writing anyway would leak storage permanently.
        if db.documents.find_one({"_id": document_id}) is None:
            return
        s3.upload(storage_path, BytesIO(searchable_pdf), "application/pdf")
        db.documents.update_one(
            {"_id": document_id}, {"$set": {"file_size_bytes": len(searchable_pdf)}}
        )
    except Exception:
        logger.exception("failed to write searchable PDF for document %s", document_id)


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

        result = extract_pages(
            file_bytes, doc["mime_type"], make_searchable=doc.get("source") == "mobile_scan"
        )
        pages = result.page_texts
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

        if result.searchable_pdf is not None:
            _write_searchable_pdf(db, document_id, doc["storage_path"], result.searchable_pdf)
    except Exception as exc:
        _set_status(db, document_id, "failed", processing_error=str(exc))
