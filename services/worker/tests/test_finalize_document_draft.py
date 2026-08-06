import importlib
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image

from tests.fakes import FakeDb, FakeS3

# See test_send_overdue_digest.py's identical comment: app/tasks/__init__.py
# clobbers app.tasks.finalize_document_draft's module attribute with the
# task function of the same name, so importlib is needed to get the module.
finalize_module = importlib.import_module("app.tasks.finalize_document_draft")


def _jpeg_bytes(size=(20, 10)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color="blue").save(buf, format="JPEG")
    return buf.getvalue()


def _draft(*, status="finalizing", page_paths=("d1/p1.jpg", "d1/p2.jpg"), name=None):
    return {
        "_id": "draft1",
        "household_id": "house1",
        "entity_ids": ["entity1"],
        "document_type": "manual",
        "shared_with": "household",
        "created_by": "user1",
        "created_at": datetime.now(timezone.utc),
        "last_activity_at": datetime.now(timezone.utc),
        "pages": [{"id": f"page{i}", "storage_path": p, "mime_type": "image/jpeg"} for i, p in enumerate(page_paths)],
        "name": name,
        "status": status,
        "resulting_document_id": None,
        "finalize_error": None,
    }


class FakeProcessDocument:
    def __init__(self):
        self.calls = []

    def delay(self, document_id):
        self.calls.append(document_id)


def _setup(monkeypatch, draft, *, entity=None, user=None):
    entity = entity if entity is not None else {
        "_id": "entity1",
        "household_id": "house1",
        "domain": "vehicle",
        "created_by": "user1",
        "shared_with": "household",
        "archived_at": None,
        "pending_delete_at": None,
    }
    user = user if user is not None else {"_id": "user1", "household_id": "house1", "role": "owner"}

    db = FakeDb(
        document_drafts=[draft],
        entities=[entity] if entity else [],
        users=[user] if user else [],
        documents=[],
    )
    fake_s3 = FakeS3()
    for page in draft["pages"]:
        fake_s3.objects[page["storage_path"]] = _jpeg_bytes()

    fake_process_document = FakeProcessDocument()

    monkeypatch.setattr(finalize_module, "get_db", lambda: db)
    monkeypatch.setattr(finalize_module, "s3", fake_s3)
    monkeypatch.setattr(finalize_module, "process_document", fake_process_document)

    return db, fake_s3, fake_process_document


def test_success_path_creates_document_and_cleans_up_pages(monkeypatch):
    draft = _draft()
    db, fake_s3, fake_process_document = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    updated = db.document_drafts.find_one({"_id": draft["_id"]})
    assert updated["status"] == "finalized"
    assert updated["resulting_document_id"] is not None
    # Page-less, not just missing files it still claims to have.
    assert updated["pages"] == []

    documents = db.documents.find({})
    assert len(documents) == 1
    document = documents[0]
    assert document["_id"] == updated["resulting_document_id"]
    assert document["mime_type"] == "application/pdf"
    assert document["entity_ids"] == draft["entity_ids"]
    assert document["shared_with"] == draft["shared_with"]
    # No name given — falls back to the default, matching pre-naming
    # behavior exactly.
    assert document["original_filename"] == "mobile-scan.pdf"
    assert document["storage_path"].endswith("/mobile-scan.pdf")

    # Draft pages are gone from S3; the assembled PDF is present instead.
    for page in draft["pages"]:
        assert page["storage_path"] not in fake_s3.objects
    assert document["storage_path"] in fake_s3.objects

    assert fake_process_document.calls == [document["_id"]]


def test_custom_name_becomes_filename_with_pdf_extension(monkeypatch):
    draft = _draft(name="Water Heater Manual")
    db, fake_s3, _ = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    document = db.documents.find({})[0]
    assert document["original_filename"] == "Water Heater Manual.pdf"
    assert document["storage_path"].endswith("/Water Heater Manual.pdf")
    assert document["storage_path"] in fake_s3.objects


def test_custom_name_already_ending_in_pdf_is_not_duplicated(monkeypatch):
    draft = _draft(name="Warranty.pdf")
    db, _, _ = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    document = db.documents.find({})[0]
    assert document["original_filename"] == "Warranty.pdf"


def test_custom_name_sanitized_for_storage_path(monkeypatch):
    draft = _draft(name="../../etc/passwd")
    db, _, _ = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    document = db.documents.find({})[0]
    # No embedded "/" — the sanitized name can't add extra path segments
    # and escape the `{household}/{document}/` prefix in the S3 key.
    assert "/" not in document["original_filename"]
    filename_segment = document["storage_path"].split("/", 2)[2]
    assert filename_segment == document["original_filename"]


def test_blank_name_falls_back_to_default(monkeypatch):
    draft = _draft(name="   ")
    db, _, _ = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    document = db.documents.find({})[0]
    assert document["original_filename"] == "mobile-scan.pdf"


def test_oversize_pdf_fails_then_retry_succeeds(monkeypatch):
    draft = _draft()
    db, fake_s3, fake_process_document = _setup(monkeypatch, draft)
    monkeypatch.setattr(finalize_module.settings, "max_upload_bytes", 10)

    finalize_module.finalize_document_draft(draft["_id"])

    failed = db.document_drafts.find_one({"_id": draft["_id"]})
    assert failed["status"] == "failed"
    assert "maximum upload size" in failed["finalize_error"]
    # Pages and draft left in place for retry.
    assert len(failed["pages"]) == len(draft["pages"])
    for page in draft["pages"]:
        assert page["storage_path"] in fake_s3.objects
    assert db.documents.find({}) == []

    # Retry: raise the limit back up and re-enter from `failed` (the
    # finalize endpoint flips status back to `finalizing` on retry).
    monkeypatch.setattr(finalize_module.settings, "max_upload_bytes", 25 * 1024 * 1024)
    db.document_drafts.update_one({"_id": draft["_id"]}, {"$set": {"status": "finalizing"}})

    finalize_module.finalize_document_draft(draft["_id"])

    succeeded = db.document_drafts.find_one({"_id": draft["_id"]})
    assert succeeded["status"] == "finalized"
    assert len(db.documents.find({})) == 1


def test_archived_entity_fails_without_creating_document(monkeypatch):
    draft = _draft()
    archived_entity = {
        "_id": "entity1",
        "household_id": "house1",
        "domain": "vehicle",
        "created_by": "user1",
        "shared_with": "household",
        "archived_at": datetime.now(timezone.utc),
        "pending_delete_at": None,
    }
    db, fake_s3, fake_process_document = _setup(monkeypatch, draft, entity=archived_entity)

    finalize_module.finalize_document_draft(draft["_id"])

    failed = db.document_drafts.find_one({"_id": draft["_id"]})
    assert failed["status"] == "failed"
    assert failed["finalize_error"] == "entity no longer accessible"
    assert db.documents.find({}) == []
    # Pages untouched — the entity-access failure happens before any S3
    # mutation.
    for page in draft["pages"]:
        assert page["storage_path"] in fake_s3.objects
    assert fake_process_document.calls == []


def test_unshared_entity_fails_when_draft_creator_lacks_access(monkeypatch):
    draft = _draft()
    narrowly_shared_entity = {
        "_id": "entity1",
        "household_id": "house1",
        "domain": "vehicle",
        "created_by": "someone-else",
        "shared_with": ["a-different-user"],
        "archived_at": None,
        "pending_delete_at": None,
    }
    member_user = {"_id": "user1", "household_id": "house1", "role": "member"}
    db, _, fake_process_document = _setup(
        monkeypatch, draft, entity=narrowly_shared_entity, user=member_user
    )

    finalize_module.finalize_document_draft(draft["_id"])

    failed = db.document_drafts.find_one({"_id": draft["_id"]})
    assert failed["status"] == "failed"
    assert failed["finalize_error"] == "entity no longer accessible"
    assert fake_process_document.calls == []


def test_ignores_draft_not_in_finalizing_status(monkeypatch):
    draft = _draft(status="capturing")
    db, fake_s3, fake_process_document = _setup(monkeypatch, draft)

    finalize_module.finalize_document_draft(draft["_id"])

    untouched = db.document_drafts.find_one({"_id": draft["_id"]})
    assert untouched["status"] == "capturing"
    assert db.documents.find({}) == []
    assert fake_process_document.calls == []


def test_missing_draft_is_a_noop(monkeypatch):
    db = FakeDb(document_drafts=[], entities=[], users=[], documents=[])
    monkeypatch.setattr(finalize_module, "get_db", lambda: db)

    finalize_module.finalize_document_draft("does-not-exist")  # must not raise
