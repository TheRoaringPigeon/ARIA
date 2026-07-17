import asyncio
import logging

from app import core_api_client, retrieval
from app.schemas.chat import Citation

logger = logging.getLogger(__name__)


def _dedup_candidates(
    chunks: list[retrieval.RetrievedChunk],
) -> list[retrieval.RetrievedChunk]:
    """Keeps the first chunk seen per `(document_id, page_number)` — chunks
    arrive distance-sorted from Chroma, so "first seen" is also "best
    match" for that page. Two chunks off the same page (a page split across
    `CHUNK_FLUSH_THRESHOLD`) or the same document on different pages would
    otherwise produce duplicate/redundant citations.
    """
    seen: set[tuple[str, int]] = set()
    candidates = []
    for chunk in chunks:
        key = (chunk.mongo_document_id, chunk.page_number)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(chunk)
    return candidates


async def _safe_get_document(cookie: str, document_id: str) -> dict | None:
    """Isolates one document lookup's failure from the rest of the batch —
    a deleted document (404) or a cross-household Chroma leak (M4's known
    follow-up: retrieval isn't household-scoped) shouldn't wipe out every
    other citation, just its own.
    """
    try:
        return await core_api_client.get_document(cookie, document_id)
    except Exception:
        logger.warning(
            "failed to resolve document %s for citation, dropping it", document_id, exc_info=True
        )
        return None


async def resolve_citations(
    cookie: str | None, chunks: list[retrieval.RetrievedChunk]
) -> list[Citation]:
    """Resolve retrieved chunks into user-facing citations.

    Degrades to `[]` on no cookie, no chunks, or any per-document lookup
    failure — citations are additive to the chat answer, never load-bearing
    for it, mirroring `entity_grounding.py`'s contract.
    """
    if cookie is None or not chunks:
        return []

    candidates = _dedup_candidates(chunks)

    unique_document_ids = list({chunk.mongo_document_id for chunk in candidates})
    resolved = await asyncio.gather(
        *(_safe_get_document(cookie, document_id) for document_id in unique_document_ids)
    )
    filenames_by_document_id = {
        document_id: document.get("original_filename")
        for document_id, document in zip(unique_document_ids, resolved)
        if document is not None
    }

    citations = []
    for chunk in candidates:
        filename = filenames_by_document_id.get(chunk.mongo_document_id)
        if filename is None:
            continue
        citations.append(
            Citation(
                document_id=chunk.mongo_document_id,
                filename=filename,
                page_number=chunk.page_number,
                section_header=chunk.section_header,
            )
        )
    return citations
