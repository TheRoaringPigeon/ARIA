from app.backfill_household_id import backfill_household_id


class FakeMongoCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection):
        return self._docs


class FakeMongoDb:
    def __init__(self, documents):
        self.documents = FakeMongoCollection(documents)


class FakeChromaCollection:
    def __init__(self, chunks_by_document_id):
        """`chunks_by_document_id`: {mongo_document_id: [(chunk_id, metadata), ...]}"""
        self._chunks_by_document_id = chunks_by_document_id
        self.update_calls = []

    def get(self, where, include):
        chunks = self._chunks_by_document_id.get(where["mongo_document_id"], [])
        return {"ids": [c[0] for c in chunks], "metadatas": [c[1] for c in chunks]}

    def update(self, ids, metadatas):
        self.update_calls.append((ids, metadatas))
        # Reflect the update back into the fake store so a second backfill
        # pass sees the now-tagged metadata, proving idempotency.
        for document_id, chunks in self._chunks_by_document_id.items():
            updated = []
            for chunk_id, metadata in chunks:
                if chunk_id in ids:
                    metadata = metadatas[ids.index(chunk_id)]
                updated.append((chunk_id, metadata))
            self._chunks_by_document_id[document_id] = updated


def test_backfills_only_chunks_missing_household_id():
    mongo_db = FakeMongoDb([{"_id": "doc1", "household_id": "h1"}])
    collection = FakeChromaCollection(
        {
            "doc1": [
                ("doc1:0", {"mongo_document_id": "doc1", "chunk_index": 0}),
                ("doc1:1", {"mongo_document_id": "doc1", "chunk_index": 1, "household_id": "h1"}),
            ]
        }
    )

    documents_touched, chunks_touched = backfill_household_id(mongo_db, collection)

    assert documents_touched == 1
    assert chunks_touched == 1
    updated_ids, updated_metadatas = collection.update_calls[0]
    assert updated_ids == ["doc1:0"]
    assert updated_metadatas[0]["household_id"] == "h1"


def test_already_tagged_chunks_left_untouched():
    mongo_db = FakeMongoDb([{"_id": "doc1", "household_id": "h1"}])
    collection = FakeChromaCollection(
        {"doc1": [("doc1:0", {"mongo_document_id": "doc1", "chunk_index": 0, "household_id": "h1"})]}
    )

    documents_touched, chunks_touched = backfill_household_id(mongo_db, collection)

    assert documents_touched == 0
    assert chunks_touched == 0
    assert collection.update_calls == []


def test_idempotent_rerun_touches_nothing():
    mongo_db = FakeMongoDb([{"_id": "doc1", "household_id": "h1"}])
    collection = FakeChromaCollection(
        {"doc1": [("doc1:0", {"mongo_document_id": "doc1", "chunk_index": 0})]}
    )

    backfill_household_id(mongo_db, collection)
    documents_touched, chunks_touched = backfill_household_id(mongo_db, collection)

    assert documents_touched == 0
    assert chunks_touched == 0


def test_multiple_documents_backfilled_independently():
    mongo_db = FakeMongoDb(
        [{"_id": "doc1", "household_id": "h1"}, {"_id": "doc2", "household_id": "h2"}]
    )
    collection = FakeChromaCollection(
        {
            "doc1": [("doc1:0", {"mongo_document_id": "doc1", "chunk_index": 0})],
            "doc2": [("doc2:0", {"mongo_document_id": "doc2", "chunk_index": 0})],
        }
    )

    documents_touched, chunks_touched = backfill_household_id(mongo_db, collection)

    assert documents_touched == 2
    assert chunks_touched == 2
    assert collection._chunks_by_document_id["doc1"][0][1]["household_id"] == "h1"
    assert collection._chunks_by_document_id["doc2"][0][1]["household_id"] == "h2"
