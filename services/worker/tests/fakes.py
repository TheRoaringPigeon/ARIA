"""Shared hand-rolled fakes for worker tests that need a Mongo-ish store.

Worker uses plain pymongo (sync), and there's no mongomock dependency in
this service (see services/core-api/tests/conftest.py for the motor-based
equivalent, which doesn't apply here). These fakes implement just the
query/update subset the worker's own tasks actually use.
"""

import copy


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in" and actual not in operand:
                    return False
                if op == "$ne" and actual == operand:
                    return False
                if op == "$lt" and not (actual is not None and actual < operand):
                    return False
        elif actual != expected:
            return False
    return True


def _apply_update(doc: dict, update: dict) -> None:
    for op, fields in update.items():
        if op == "$set":
            doc.update(fields)
        else:
            raise NotImplementedError(f"FakeCollection doesn't support {op!r}")


class FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return [copy.deepcopy(d) for d in self.docs if _matches(d, query)]

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                del self.docs[i]
                return
        raise AssertionError(f"delete_one found no match for {query!r}")


class FakeDb:
    def __init__(self, **collections):
        for name, docs in collections.items():
            setattr(self, name, FakeCollection(docs))


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
