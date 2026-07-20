import logging

from chromadb.api.models.Collection import Collection
from pymongo.database import Database

from app import chroma, db

logger = logging.getLogger(__name__)


def backfill_household_id(mongo_db: Database, collection: Collection) -> tuple[int, int]:
    """Every chunk embedded before household-scoped retrieval existed is
    missing `household_id` in its Chroma metadata — this re-derives it from
    each chunk's own `mongo_document_id` -> `Document.household_id` and
    patches it in. Idempotent: a chunk that already has `household_id` is
    left untouched, so re-running this touches nothing new. Returns
    `(documents_touched, chunks_touched)` for the caller to log/report.

    A one-off pass, not a versioned migration framework — this closes one
    known-narrow gap (chunks embedded before this milestone), not an
    ongoing schema-evolution concern.
    """
    documents_touched = 0
    chunks_touched = 0

    for document in mongo_db.documents.find({}, {"_id": 1, "household_id": 1}):
        result = collection.get(where={"mongo_document_id": document["_id"]}, include=["metadatas"])
        ids = result["ids"]
        metadatas = result["metadatas"]

        stale_ids = []
        stale_metadatas = []
        for chunk_id, metadata in zip(ids, metadatas):
            if "household_id" in metadata:
                continue
            stale_ids.append(chunk_id)
            stale_metadatas.append({**metadata, "household_id": document["household_id"]})

        if stale_ids:
            collection.update(ids=stale_ids, metadatas=stale_metadatas)
            chunks_touched += len(stale_ids)
            documents_touched += 1

    return documents_touched, chunks_touched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    documents_touched, chunks_touched = backfill_household_id(db.get_db(), chroma.get_documents_collection())
    logger.info(
        "backfilled household_id onto %d chunk(s) across %d document(s)",
        chunks_touched,
        documents_touched,
    )
