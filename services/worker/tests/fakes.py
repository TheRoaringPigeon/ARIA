"""Shared hand-rolled fakes for worker tests that need a Mongo-ish store.

Worker uses plain pymongo (sync), and there's no mongomock dependency in
this service (see services/core-api/tests/conftest.py for the motor-based
equivalent, which doesn't apply here). These fakes implement just the
query/update subset the worker's own tasks actually use.
"""

import copy
from contextlib import contextmanager


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in":
                    # Mongo's array-containment semantics: a list-valued
                    # field matches $in if it shares any element with the
                    # operand, not by comparing the whole list as a scalar
                    # (needed for documents.entity_ids, a multikey field).
                    if isinstance(actual, list):
                        if not any(v in operand for v in actual):
                            return False
                    elif actual not in operand:
                        return False
                elif op == "$ne":
                    if actual == operand:
                        return False
                elif op == "$lt":
                    if not (actual is not None and actual < operand):
                        return False
                else:
                    raise NotImplementedError(f"FakeCollection doesn't support operator {op!r}")
        elif isinstance(actual, list):
            # Bare equality against a list-valued field: Mongo matches if
            # the value is an element of the array, not exact-list equality.
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


def _apply_update(doc: dict, update: dict) -> None:
    for op, fields in update.items():
        if op == "$set":
            doc.update(fields)
        elif op == "$pull":
            for field, condition in fields.items():
                current = doc.get(field, [])
                if isinstance(condition, dict) and "$in" in condition:
                    operand = condition["$in"]
                    doc[field] = [v for v in current if v not in operand]
                else:
                    doc[field] = [v for v in current if v != condition]
        else:
            raise NotImplementedError(f"FakeCollection doesn't support {op!r}")


class FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class FakeDeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]

    def find_one(self, query, projection=None, session=None):
        for d in self.docs:
            if _matches(d, query):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None, session=None):
        query = query or {}
        return [copy.deepcopy(d) for d in self.docs if _matches(d, query)]

    def insert_one(self, doc, session=None):
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, query, update, session=None):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    def update_many(self, query, update, session=None):
        matched = 0
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                matched += 1
        return FakeUpdateResult(matched)

    def delete_one(self, query, session=None):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                del self.docs[i]
                return
        raise AssertionError(f"delete_one found no match for {query!r}")

    def delete_many(self, query, session=None):
        remaining, deleted = [], 0
        for d in self.docs:
            if _matches(d, query):
                deleted += 1
            else:
                remaining.append(d)
        self.docs = remaining
        return FakeDeleteResult(deleted)


class FakeDb:
    def __init__(self, **collections):
        for name, docs in collections.items():
            setattr(self, name, FakeCollection(docs))


@contextmanager
def fake_transaction():
    """Stand-in for app/db.py's start_transaction() — FakeCollection has no
    concept of sessions/rollback (same gap mongomock has for core-api, see
    that service's tests/conftest.py's mock_transaction fixture), so this is
    a no-op that just yields None; every FakeCollection method accepts and
    ignores a `session=` kwarg. Real commit/rollback behavior is proved by
    tests/test_db_transactions.py's control-flow tests plus live
    verification against the real compose stack, not here.
    """
    yield None


class FakeS3:
    """In-memory stand-in for both core-api's and worker's app/s3.py."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload(self, key, fileobj, content_type):
        self.objects[key] = fileobj.read()

    def download(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)
