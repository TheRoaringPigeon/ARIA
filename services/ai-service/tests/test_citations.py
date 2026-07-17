import app.citations as citations_module
import app.core_api_client as core_api_client_module
from app.citations import resolve_citations
from app.retrieval import RetrievedChunk


def _chunk(document_id, page_number, section_header=None, distance=0.1):
    return RetrievedChunk(
        text="excerpt",
        mongo_document_id=document_id,
        page_number=page_number,
        chunk_index=0,
        section_header=section_header,
        distance=distance,
    )


async def test_resolve_citations_returns_empty_without_cookie(monkeypatch):
    calls = []

    async def fake_get_document(cookie, document_id):
        calls.append(document_id)
        return {"original_filename": "manual.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    result = await resolve_citations(None, [_chunk("doc1", 4)])

    assert result == []
    assert calls == []


async def test_resolve_citations_returns_empty_without_chunks(monkeypatch):
    calls = []

    async def fake_get_document(cookie, document_id):
        calls.append(document_id)
        return {"original_filename": "manual.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    result = await resolve_citations("a-cookie", [])

    assert result == []
    assert calls == []


async def test_resolve_citations_dedups_same_document_and_page(monkeypatch):
    calls = []

    async def fake_get_document(cookie, document_id):
        calls.append(document_id)
        return {"original_filename": "manual.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    chunks = [_chunk("doc1", 4, section_header="Maintenance"), _chunk("doc1", 4)]
    result = await resolve_citations("a-cookie", chunks)

    assert len(result) == 1
    assert result[0].document_id == "doc1"
    assert result[0].page_number == 4
    assert result[0].filename == "manual.pdf"
    assert result[0].section_header == "Maintenance"
    assert calls == ["doc1"]


async def test_resolve_citations_one_call_per_document_across_pages(monkeypatch):
    calls = []

    async def fake_get_document(cookie, document_id):
        calls.append(document_id)
        return {"original_filename": "manual.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    chunks = [_chunk("doc1", 4), _chunk("doc1", 7)]
    result = await resolve_citations("a-cookie", chunks)

    assert len(result) == 2
    assert {c.page_number for c in result} == {4, 7}
    assert calls == ["doc1"]


async def test_resolve_citations_isolates_a_failed_lookup(monkeypatch):
    async def fake_get_document(cookie, document_id):
        if document_id == "doc-missing":
            raise RuntimeError("404")
        return {"original_filename": f"{document_id}.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    chunks = [_chunk("doc1", 1, distance=0.1), _chunk("doc-missing", 1, distance=0.2)]
    result = await resolve_citations("a-cookie", chunks)

    assert len(result) == 1
    assert result[0].document_id == "doc1"


async def test_resolve_citations_drops_a_document_missing_filename(monkeypatch):
    async def fake_get_document(cookie, document_id):
        if document_id == "doc-malformed":
            return {"id": document_id}
        return {"original_filename": f"{document_id}.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    chunks = [_chunk("doc1", 1, distance=0.1), _chunk("doc-malformed", 1, distance=0.2)]
    result = await resolve_citations("a-cookie", chunks)

    assert len(result) == 1
    assert result[0].document_id == "doc1"


async def test_resolve_citations_includes_entity_ids(monkeypatch):
    async def fake_get_document(cookie, document_id):
        return {"original_filename": "manual.pdf", "entity_ids": ["entity1", "entity2"]}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    result = await resolve_citations("a-cookie", [_chunk("doc1", 4)])

    assert result[0].entity_ids == ["entity1", "entity2"]


async def test_resolve_citations_defaults_entity_ids_when_missing(monkeypatch):
    async def fake_get_document(cookie, document_id):
        return {"original_filename": "manual.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    result = await resolve_citations("a-cookie", [_chunk("doc1", 4)])

    assert result[0].entity_ids == []


async def test_resolve_citations_preserves_distance_order(monkeypatch):
    async def fake_get_document(cookie, document_id):
        return {"original_filename": f"{document_id}.pdf"}

    monkeypatch.setattr(core_api_client_module, "get_document", fake_get_document)

    chunks = [_chunk("doc1", 1, distance=0.1), _chunk("doc2", 1, distance=0.2)]
    result = await resolve_citations("a-cookie", chunks)

    assert [c.document_id for c in result] == ["doc1", "doc2"]


async def test_dedup_candidates_is_used(monkeypatch):
    """Sanity check that `_dedup_candidates` is the single source of truth
    for ordering/dedup, rather than duplicated inline in `resolve_citations`.
    """
    chunks = [_chunk("doc1", 1), _chunk("doc1", 1), _chunk("doc2", 1)]
    candidates = citations_module._dedup_candidates(chunks)

    assert [(c.mongo_document_id, c.page_number) for c in candidates] == [
        ("doc1", 1),
        ("doc2", 1),
    ]
