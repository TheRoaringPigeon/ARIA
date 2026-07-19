import asyncio
import logging
from dataclasses import dataclass

from app import chroma, ollama
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    text: str
    mongo_document_id: str
    page_number: int
    chunk_index: int
    section_header: str | None
    distance: float


def _query_collection(embedding: list[float]) -> dict:
    """Runs entirely inside `asyncio.to_thread` — both the (synchronous)
    collection lookup and the query itself do blocking HTTP, so neither may
    run directly on the event loop.
    """
    return chroma.get_documents_collection().query(
        query_embeddings=[embedding], n_results=settings.rag_top_k
    )


def _build_chunk(text: str, metadata: dict, distance: float) -> RetrievedChunk | None:
    try:
        return RetrievedChunk(
            text=text,
            mongo_document_id=metadata["mongo_document_id"],
            page_number=metadata["page_number"],
            chunk_index=metadata["chunk_index"],
            section_header=metadata.get("section_header"),
            distance=distance,
        )
    except KeyError:
        logger.warning("skipping chunk with malformed metadata: %r", metadata)
        return None


def dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Keeps the first-seen chunk per `(mongo_document_id, chunk_index)` —
    used when chunks are accumulated across multiple retrieval calls (e.g.
    `research_node`'s iterative tool loop) that can return overlapping
    results for related/reformulated queries. Without this, the same
    excerpt gets rendered twice into the prompt (caught in code review).
    """
    seen: set[tuple[str, int]] = set()
    deduped = []
    for chunk in chunks:
        key = (chunk.mongo_document_id, chunk.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


async def retrieve_context(query: str) -> list[RetrievedChunk]:
    """Embed `query` and return the top-k most similar chunks from Chroma,
    dropped to whichever are within `settings.rag_max_distance` — Chroma's
    `n_results` always returns `rag_top_k` chunks even when none of them are
    actually related to the query, so top-k alone isn't a relevance filter.

    Any failure (Ollama unreachable, Chroma unreachable) degrades to an
    empty result rather than raising, so a chat request always falls back
    to ungrounded, M3-style behavior instead of failing outright — retrieval
    is additive, not load-bearing, per the roadmap's strict-decoupling
    principle. A single malformed chunk is skipped rather than discarding
    the whole batch — see `_build_chunk`.
    """
    try:
        embedding = await ollama.embed(query)
        if not embedding:
            return []

        result = await asyncio.to_thread(_query_collection, embedding)

        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]

        chunks = (
            _build_chunk(text, metadata, distance)
            for text, metadata, distance in zip(documents, metadatas, distances)
        )
        return [
            chunk
            for chunk in chunks
            if chunk is not None and chunk.distance <= settings.rag_max_distance
        ]
    except Exception:
        logger.warning("retrieval failed, degrading to ungrounded chat", exc_info=True)
        return []
