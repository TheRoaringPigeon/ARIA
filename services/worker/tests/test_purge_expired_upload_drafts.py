import importlib
from datetime import datetime, timedelta, timezone

from tests.fakes import FakeDb, FakeS3

purge_module = importlib.import_module("app.tasks.purge_expired_upload_drafts")


def _draft(draft_id, *, created_at, last_activity_at, pages=None):
    return {
        "_id": draft_id,
        "household_id": "house1",
        "entity_ids": ["entity1"],
        "document_type": "manual",
        "shared_with": "household",
        "created_by": "user1",
        "created_at": created_at,
        "last_activity_at": last_activity_at,
        "pages": pages if pages is not None else [{"id": "p1", "storage_path": f"{draft_id}/p1.jpg", "mime_type": "image/jpeg"}],
        "status": "capturing",
        "resulting_document_id": None,
        "finalize_error": None,
    }


def test_purges_draft_stale_by_last_activity(monkeypatch):
    now = datetime.now(timezone.utc)
    ttl_hours = purge_module.settings.upload_draft_ttl_hours
    stale = _draft("stale", created_at=now - timedelta(hours=ttl_hours + 10), last_activity_at=now - timedelta(hours=ttl_hours + 1))

    db = FakeDb(document_drafts=[stale])
    fake_s3 = FakeS3()
    fake_s3.objects["stale/p1.jpg"] = b"fake-bytes"
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    monkeypatch.setattr(purge_module, "s3", fake_s3)

    result = purge_module.purge_expired_upload_drafts()

    assert result == {"purged": 1}
    assert db.document_drafts.find({}) == []
    assert "stale/p1.jpg" not in fake_s3.objects


def test_leaves_recently_active_draft_alone_despite_old_created_at(monkeypatch):
    """Regression test for the fixed-TTL-from-creation approach this
    last_activity_at-based design replaces: a draft resumed and actively
    edited after days of inactivity must not be purged just because it's
    old.
    """
    now = datetime.now(timezone.utc)
    ttl_hours = purge_module.settings.upload_draft_ttl_hours
    resumed = _draft(
        "resumed",
        created_at=now - timedelta(hours=ttl_hours * 3),
        last_activity_at=now - timedelta(minutes=5),
    )

    db = FakeDb(document_drafts=[resumed])
    fake_s3 = FakeS3()
    fake_s3.objects["resumed/p1.jpg"] = b"fake-bytes"
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    monkeypatch.setattr(purge_module, "s3", fake_s3)

    result = purge_module.purge_expired_upload_drafts()

    assert result == {"purged": 0}
    assert len(db.document_drafts.find({})) == 1
    assert "resumed/p1.jpg" in fake_s3.objects


def test_purges_multiple_pages_per_draft(monkeypatch):
    now = datetime.now(timezone.utc)
    ttl_hours = purge_module.settings.upload_draft_ttl_hours
    stale = _draft(
        "stale",
        created_at=now - timedelta(hours=ttl_hours + 10),
        last_activity_at=now - timedelta(hours=ttl_hours + 1),
        pages=[
            {"id": "p1", "storage_path": "stale/p1.jpg", "mime_type": "image/jpeg"},
            {"id": "p2", "storage_path": "stale/p2.jpg", "mime_type": "image/jpeg"},
        ],
    )

    db = FakeDb(document_drafts=[stale])
    fake_s3 = FakeS3()
    fake_s3.objects["stale/p1.jpg"] = b"a"
    fake_s3.objects["stale/p2.jpg"] = b"b"
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    monkeypatch.setattr(purge_module, "s3", fake_s3)

    result = purge_module.purge_expired_upload_drafts()

    assert result == {"purged": 1}
    assert fake_s3.objects == {}
