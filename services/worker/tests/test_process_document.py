import importlib
import logging
from datetime import datetime, timezone

import pytest

from app.logic.ocr import OcrResult
from tests.fakes import FakeDb, FakeS3

# See test_finalize_document_draft.py's identical comment: app/tasks/__init__.py
# clobbers app.tasks.process_document's module attribute with the task
# function of the same name, so importlib is needed to get the module.
process_module = importlib.import_module("app.tasks.process_document")

_ORIGINAL_BYTES = b"original-pdf-bytes"


def _document(*, source=None, storage_path="house1/doc1/mobile-scan.pdf"):
    doc = {
        "_id": "doc1",
        "household_id": "house1",
        "entity_ids": [],
        "log_ids": [],
        "document_type": "manual",
        "original_filename": "mobile-scan.pdf",
        "storage_path": storage_path,
        "mime_type": "application/pdf",
        "file_size_bytes": len(_ORIGINAL_BYTES),
        "page_count": None,
        "processing_status": "pending",
        "processing_error": None,
        "shared_with": "household",
        "uploaded_by": "user1",
        "uploaded_at": datetime.now(timezone.utc),
    }
    if source is not None:
        doc["source"] = source
    return doc


class FakeChromaCollection:
    def __init__(self):
        self.added = []

    def add(self, ids, embeddings, documents, metadatas):
        self.added.append(
            {"ids": ids, "embeddings": embeddings, "documents": documents, "metadatas": metadatas}
        )


class FakeChroma:
    def __init__(self):
        self.collection = FakeChromaCollection()

    def get_documents_collection(self):
        return self.collection


class FakeOllama:
    def embed_batch(self, texts):
        return [[0.0, 0.0] for _ in texts]


def _setup(monkeypatch, doc):
    db = FakeDb(documents=[doc])
    fake_s3 = FakeS3()
    fake_s3.objects[doc["storage_path"]] = _ORIGINAL_BYTES
    fake_chroma = FakeChroma()

    monkeypatch.setattr(process_module, "get_db", lambda: db)
    monkeypatch.setattr(process_module, "s3", fake_s3)
    monkeypatch.setattr(process_module, "chroma", fake_chroma)
    monkeypatch.setattr(process_module, "ollama", FakeOllama())

    return db, fake_s3, fake_chroma


def test_mobile_scan_document_rewrites_storage_and_updates_file_size(monkeypatch):
    doc = _document(source="mobile_scan")
    db, fake_s3, _ = _setup(monkeypatch, doc)
    searchable_bytes = b"searchable-pdf-bytes"

    def fake_extract_pages(file_bytes, mime_type, make_searchable=False):
        assert make_searchable is True
        return OcrResult(page_texts=["Some OCR text long enough to form a chunk."], searchable_pdf=searchable_bytes)

    monkeypatch.setattr(process_module, "extract_pages", fake_extract_pages)

    process_module.process_document(doc["_id"])

    assert fake_s3.objects[doc["storage_path"]] == searchable_bytes
    updated = db.documents.find_one({"_id": doc["_id"]})
    assert updated["file_size_bytes"] == len(searchable_bytes)
    assert updated["processing_status"] == "embedded"


@pytest.mark.parametrize("source", ["upload", None])
def test_non_mobile_scan_document_never_rewrites_storage(monkeypatch, source):
    doc = _document(source=source)
    db, fake_s3, _ = _setup(monkeypatch, doc)

    def fake_extract_pages(file_bytes, mime_type, make_searchable=False):
        assert make_searchable is False
        return OcrResult(page_texts=["Some OCR text long enough to form a chunk."])

    monkeypatch.setattr(process_module, "extract_pages", fake_extract_pages)

    process_module.process_document(doc["_id"])

    assert fake_s3.objects[doc["storage_path"]] == _ORIGINAL_BYTES
    updated = db.documents.find_one({"_id": doc["_id"]})
    assert updated["processing_status"] == "embedded"


def test_oversize_searchable_pdf_does_not_fail_the_pipeline(monkeypatch, caplog):
    doc = _document(source="mobile_scan")
    db, fake_s3, fake_chroma = _setup(monkeypatch, doc)
    monkeypatch.setattr(process_module.settings, "max_upload_bytes", 10)

    def fake_extract_pages(file_bytes, mime_type, make_searchable=False):
        return OcrResult(
            page_texts=["Some OCR text long enough to form a chunk."],
            searchable_pdf=b"x" * 100,
        )

    monkeypatch.setattr(process_module, "extract_pages", fake_extract_pages)

    with caplog.at_level(logging.ERROR, logger=process_module.logger.name):
        process_module.process_document(doc["_id"])

    # Rewrite never happened, but OCR/chunk/embed still completed fully.
    assert fake_s3.objects[doc["storage_path"]] == _ORIGINAL_BYTES
    updated = db.documents.find_one({"_id": doc["_id"]})
    assert updated["processing_status"] == "embedded"
    assert len(fake_chroma.collection.added) == 1
    assert "failed to write searchable PDF" in caplog.text


def test_document_deleted_during_ocr_skips_searchable_pdf_write(monkeypatch):
    doc = _document(source="mobile_scan")
    db, fake_s3, _ = _setup(monkeypatch, doc)

    def fake_extract_pages(file_bytes, mime_type, make_searchable=False):
        # Simulate a concurrent DELETE /documents/{id} finishing (row gone,
        # S3 delete enqueued) while this task was still mid-OCR.
        db.documents.delete_one({"_id": doc["_id"]})
        return OcrResult(page_texts=[""], searchable_pdf=b"searchable-pdf-bytes")

    monkeypatch.setattr(process_module, "extract_pages", fake_extract_pages)

    process_module.process_document(doc["_id"])  # must not raise

    assert fake_s3.objects[doc["storage_path"]] == _ORIGINAL_BYTES
    assert db.documents.find({}) == []
