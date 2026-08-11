import importlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.fakes import FakeDb, fake_transaction

# Same importlib workaround as test_purge_expired_upload_drafts.py — see that
# file's comment: app/tasks/__init__.py's `from app.tasks.X import X` clobbers
# `app.tasks.X` (the submodule attribute) with `X` (the function), so a plain
# `import app.tasks.purge_expired_trash` resolves to the function, not the
# module. importlib reads straight out of sys.modules and sidesteps it.
purge_module = importlib.import_module("app.tasks.purge_expired_trash")


@pytest.fixture(autouse=True)
def _no_txn(monkeypatch):
    """FakeCollection has no real session/transaction support (see
    tests/fakes.py's fake_transaction docstring) — patched in for every test
    in this file since purge_expired_trash now wraps its whole cascade in
    start_transaction().
    """
    monkeypatch.setattr(purge_module, "start_transaction", fake_transaction)


def _entity(entity_id, *, household_id="house1", pending_delete_at):
    return {"_id": entity_id, "household_id": household_id, "pending_delete_at": pending_delete_at}


def _log(log_id, entity_id, household_id="house1"):
    return {"_id": log_id, "entity_id": entity_id, "household_id": household_id}


def _schedule(schedule_id, entity_id, household_id="house1"):
    return {"_id": schedule_id, "entity_id": entity_id, "household_id": household_id}


def _document(doc_id, entity_ids, log_ids=None, household_id="house1"):
    return {
        "_id": doc_id,
        "household_id": household_id,
        "entity_ids": list(entity_ids),
        "log_ids": list(log_ids or []),
        "storage_path": f"{doc_id}/path",
    }


def _user(user_id, pinned_entity_ids, household_id="house1"):
    return {"_id": user_id, "household_id": household_id, "pinned_entity_ids": list(pinned_entity_ids)}


def _expired(hours_ago=1):
    return datetime.now(timezone.utc) - timedelta(hours=purge_module.settings.entity_trash_grace_hours + hours_ago)


def _make_db(**collections):
    db = FakeDb(**collections)
    return db


def test_full_cascade_single_entity(monkeypatch):
    db = _make_db(
        entities=[_entity("e1", pending_delete_at=_expired())],
        logs=[_log("l1", "e1")],
        schedules=[_schedule("s1", "e1")],
        documents=[_document("d1", entity_ids=["e1", "e2"])],
        users=[_user("u1", pinned_entity_ids=["e1", "e3"])],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    delay_calls = []
    monkeypatch.setattr(purge_module.delete_document, "delay", lambda *a: delay_calls.append(a))

    result = purge_module.purge_expired_trash()

    assert result == {"purged": 1}
    assert db.entities.find_one({"_id": "e1"}) is None
    assert db.logs.find({"entity_id": "e1"}) == []
    assert db.schedules.find({"entity_id": "e1"}) == []
    doc = db.documents.find_one({"_id": "d1"})
    assert doc["entity_ids"] == ["e2"]
    assert delay_calls == []
    user = db.users.find_one({"_id": "u1"})
    assert user["pinned_entity_ids"] == ["e3"]


def test_orphaned_document_enqueued_for_deletion(monkeypatch):
    db = _make_db(
        entities=[_entity("e1", pending_delete_at=_expired())],
        logs=[],
        schedules=[],
        documents=[_document("d1", entity_ids=["e1"], log_ids=[])],
        users=[],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    delay_calls = []
    monkeypatch.setattr(purge_module.delete_document, "delay", lambda *a: delay_calls.append(a))

    result = purge_module.purge_expired_trash()

    assert result == {"purged": 1}
    assert delay_calls == [("d1", "d1/path")]
    # The Mongo row itself is gone synchronously, in the same transaction
    # as the entity unlink — an orphaned document (empty entity_ids) is
    # never a persisted, queryable state, not even briefly while the
    # delay() enqueue above is waiting for a worker to pick it up.
    assert db.documents.find_one({"_id": "d1"}) is None


def test_orphaned_document_deleted_even_if_delay_enqueue_never_runs(monkeypatch):
    """Proves the row deletion doesn't depend on delete_document.delay()
    actually firing — it's a plain Mongo write inside the transaction, not
    gated on the Celery broker being reachable.
    """
    db = _make_db(
        entities=[_entity("e1", pending_delete_at=_expired())],
        logs=[],
        schedules=[],
        documents=[_document("d1", entity_ids=["e1"], log_ids=[])],
        users=[],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)

    def _boom(*a, **k):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(purge_module.delete_document, "delay", _boom)

    with pytest.raises(RuntimeError, match="broker unreachable"):
        purge_module.purge_expired_trash()

    # The transaction (entity purge + document row delete) already
    # committed before the post-transaction delay() call raised.
    assert db.entities.find_one({"_id": "e1"}) is None
    assert db.documents.find_one({"_id": "d1"}) is None


def test_restore_race_excludes_restored_entity(monkeypatch):
    """A household member restores one entity between the initial snapshot
    and the batch delete_many — that entity (and only that one) must survive
    the whole cascade untouched, exactly like the old per-entity delete_one's
    re-conditioned filter, just applied across the batch.
    """
    db = _make_db(
        entities=[
            _entity("purged1", pending_delete_at=_expired()),
            _entity("racer", pending_delete_at=_expired()),
        ],
        logs=[_log("l1", "purged1"), _log("l2", "racer")],
        schedules=[_schedule("s1", "purged1"), _schedule("s2", "racer")],
        documents=[],
        users=[_user("u1", pinned_entity_ids=["purged1", "racer"])],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    monkeypatch.setattr(purge_module.delete_document, "delay", lambda *a: None)

    original_delete_many = db.entities.delete_many

    def racy_delete_many(query, session=None):
        # Simulate a restore-from-trash landing after purge_expired_trash's
        # snapshot find() but before this delete_many actually runs.
        for d in db.entities.docs:
            if d["_id"] == "racer":
                d["pending_delete_at"] = None
        return original_delete_many(query, session=session)

    monkeypatch.setattr(db.entities, "delete_many", racy_delete_many)

    result = purge_module.purge_expired_trash()

    assert result == {"purged": 1}
    assert db.entities.find_one({"_id": "purged1"}) is None
    racer = db.entities.find_one({"_id": "racer"})
    assert racer is not None and racer["pending_delete_at"] is None
    assert db.logs.find({"entity_id": "racer"}) != []
    assert db.schedules.find({"entity_id": "racer"}) != []
    user = db.users.find_one({"_id": "u1"})
    assert user["pinned_entity_ids"] == ["racer"]


def test_no_expired_entities_is_noop(monkeypatch):
    db = _make_db(
        entities=[_entity("alive", pending_delete_at=None)],
        logs=[_log("l1", "alive")],
        schedules=[],
        documents=[],
        users=[],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)

    def _boom(*a, **k):
        raise AssertionError("should not touch other collections when nothing is expired")

    monkeypatch.setattr(db.logs, "delete_many", _boom)
    monkeypatch.setattr(db.schedules, "delete_many", _boom)
    monkeypatch.setattr(db.documents, "update_many", _boom)
    monkeypatch.setattr(db.users, "update_many", _boom)

    result = purge_module.purge_expired_trash()

    assert result == {"purged": 0}
    assert db.entities.find_one({"_id": "alive"}) is not None


def test_multi_entity_multi_household_batch(monkeypatch):
    db = _make_db(
        entities=[
            _entity("e1", household_id="h1", pending_delete_at=_expired()),
            _entity("e2", household_id="h1", pending_delete_at=_expired()),
            _entity("e3", household_id="h2", pending_delete_at=_expired()),
            _entity("alive", household_id="h2", pending_delete_at=None),
        ],
        logs=[_log("l1", "e1", "h1"), _log("l2", "e3", "h2"), _log("l3", "alive", "h2")],
        schedules=[_schedule("s1", "e2", "h1")],
        documents=[],
        users=[
            _user("u1", pinned_entity_ids=["e1", "e2"], household_id="h1"),
            _user("u2", pinned_entity_ids=["e3", "alive"], household_id="h2"),
        ],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    monkeypatch.setattr(purge_module.delete_document, "delay", lambda *a: None)

    result = purge_module.purge_expired_trash()

    assert result == {"purged": 3}
    for eid in ("e1", "e2", "e3"):
        assert db.entities.find_one({"_id": eid}) is None
    assert db.entities.find_one({"_id": "alive"}) is not None
    assert db.logs.find({"entity_id": "e1"}) == []
    assert db.logs.find({"entity_id": "alive"}) != []
    assert db.schedules.find({"entity_id": "e2"}) == []
    u1 = db.users.find_one({"_id": "u1"})
    assert u1["pinned_entity_ids"] == []
    u2 = db.users.find_one({"_id": "u2"})
    assert u2["pinned_entity_ids"] == ["alive"]


def test_orphan_enqueue_never_fires_if_cascade_fails_midway(monkeypatch):
    """delete_document.delay() must only fire once the whole cascade is
    confirmed committed (it's the one step that can't be part of the Mongo
    transaction's rollback) — this proves that ordering: a failure partway
    through the cascade must propagate and must not have already enqueued
    anything for the entity whose orphan-check runs later in the function.
    """
    db = _make_db(
        entities=[_entity("e1", pending_delete_at=_expired())],
        logs=[],
        schedules=[],
        documents=[_document("d1", entity_ids=["e1"], log_ids=[])],
        users=[],
    )
    monkeypatch.setattr(purge_module, "get_db", lambda: db)
    delay_calls = []
    monkeypatch.setattr(purge_module.delete_document, "delay", lambda *a: delay_calls.append(a))

    def _boom(*a, **k):
        raise RuntimeError("simulated mid-cascade failure")

    monkeypatch.setattr(db.users, "update_many", _boom)

    with pytest.raises(RuntimeError, match="simulated mid-cascade failure"):
        purge_module.purge_expired_trash()

    assert delay_calls == []
