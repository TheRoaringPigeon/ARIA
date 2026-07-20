import httpx

import app.chroma as chroma_module
import app.ollama as ollama_module
from app.config import settings
from app.retrieval import RetrievedChunk, dedup_chunks, retrieve_context

CANNED_RESULT = {
    "documents": [["The oil capacity is 5 quarts.", "Tire pressure is 32 psi."]],
    "metadatas": [
        [
            {"mongo_document_id": "doc1", "page_number": 4, "chunk_index": 0, "section_header": "Maintenance"},
            {"mongo_document_id": "doc1", "page_number": 4, "chunk_index": 1},
        ]
    ],
    "distances": [[0.12, 0.31]],
}


class FakeCollection:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


async def fake_embed(text):
    return [0.1, 0.2, 0.3]


def _patch_collection(monkeypatch, fake_collection):
    async def fake_get_documents_collection_async():
        return fake_collection

    monkeypatch.setattr(
        chroma_module, "get_documents_collection_async", fake_get_documents_collection_async
    )


async def test_retrieve_context_returns_chunks(monkeypatch):
    fake_collection = FakeCollection(result=CANNED_RESULT)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)

    chunks = await retrieve_context("what's the oil capacity", "h1")

    assert len(chunks) == 2
    assert chunks[0].text == "The oil capacity is 5 quarts."
    assert chunks[0].mongo_document_id == "doc1"
    assert chunks[0].page_number == 4
    assert chunks[0].chunk_index == 0
    assert chunks[0].section_header == "Maintenance"
    assert chunks[0].distance == 0.12
    assert chunks[1].section_header is None
    assert fake_collection.calls[0]["n_results"] == settings.rag_top_k
    assert fake_collection.calls[0]["where"] == {"household_id": "h1"}


async def test_retrieve_context_respects_custom_top_k(monkeypatch):
    fake_collection = FakeCollection(result=CANNED_RESULT)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)
    monkeypatch.setattr(settings, "rag_top_k", 7)

    await retrieve_context("what's the oil capacity", "h1")

    assert fake_collection.calls[0]["n_results"] == 7


async def test_retrieve_context_degrades_on_chroma_error(monkeypatch):
    fake_collection = FakeCollection(exc=RuntimeError("chroma is down"))
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)

    assert await retrieve_context("anything", "h1") == []


async def test_retrieve_context_degrades_on_embed_error(monkeypatch):
    async def failing_embed(text):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama_module, "embed", failing_embed)

    assert await retrieve_context("anything", "h1") == []


async def test_retrieve_context_short_circuits_without_household_id(monkeypatch):
    """No household_id (no cookie, expired session, or core-api down) means
    no document grounding at all — never an unscoped query across every
    household's documents. Chroma isn't even touched in this case.
    """
    fake_collection = FakeCollection(result=CANNED_RESULT)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)

    assert await retrieve_context("anything", None) == []
    assert fake_collection.calls == []


async def test_retrieve_context_skips_malformed_chunk(monkeypatch):
    result_with_bad_chunk = {
        "documents": [["The oil capacity is 5 quarts.", "a chunk missing its metadata"]],
        "metadatas": [
            [
                {"mongo_document_id": "doc1", "page_number": 4, "chunk_index": 0, "section_header": "Maintenance"},
                {"mongo_document_id": "doc1"},
            ]
        ],
        "distances": [[0.12, 0.31]],
    }
    fake_collection = FakeCollection(result=result_with_bad_chunk)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)

    chunks = await retrieve_context("what's the oil capacity", "h1")

    assert len(chunks) == 1
    assert chunks[0].text == "The oil capacity is 5 quarts."


async def test_retrieve_context_drops_chunks_beyond_max_distance(monkeypatch):
    """`n_results` always returns `rag_top_k` chunks even when nothing in
    the corpus is actually related to the query — the distance filter is
    what keeps an unrelated document from becoming context or a citation.
    """
    result = {
        "documents": [["a genuinely relevant excerpt", "a coincidental near-miss"]],
        "metadatas": [
            [
                {"mongo_document_id": "doc1", "page_number": 1, "chunk_index": 0},
                {"mongo_document_id": "doc2", "page_number": 1, "chunk_index": 0},
            ]
        ],
        "distances": [[0.5, 1.5]],
    }
    fake_collection = FakeCollection(result=result)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)
    monkeypatch.setattr(settings, "rag_max_distance", 0.9)

    chunks = await retrieve_context("some query", "h1")

    assert len(chunks) == 1
    assert chunks[0].mongo_document_id == "doc1"


async def test_retrieve_context_keeps_chunk_exactly_at_max_distance(monkeypatch):
    result = {
        "documents": [["right at the boundary"]],
        "metadatas": [[{"mongo_document_id": "doc1", "page_number": 1, "chunk_index": 0}]],
        "distances": [[0.9]],
    }
    fake_collection = FakeCollection(result=result)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)
    monkeypatch.setattr(settings, "rag_max_distance", 0.9)

    chunks = await retrieve_context("some query", "h1")

    assert len(chunks) == 1


async def test_retrieve_context_handles_empty_collection(monkeypatch):
    empty_result = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    fake_collection = FakeCollection(result=empty_result)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    _patch_collection(monkeypatch, fake_collection)

    assert await retrieve_context("anything", "h1") == []


def _chunk(mongo_document_id, chunk_index, text="x"):
    return RetrievedChunk(
        text=text,
        mongo_document_id=mongo_document_id,
        page_number=1,
        chunk_index=chunk_index,
        section_header=None,
        distance=0.1,
    )


def test_dedup_chunks_drops_repeats_of_the_same_document_and_chunk_index():
    """Regression test (caught in code review): `research_node`'s
    tool-choice loop can search twice with related/reformulated queries
    that both return the same top chunk — without dedup, the identical
    excerpt would be rendered twice into the prompt.
    """
    first = _chunk("doc1", 0, text="first search result")
    second = _chunk("doc1", 0, text="first search result")
    other = _chunk("doc1", 1, text="a different chunk")

    assert dedup_chunks([first, second, other]) == [first, other]


def test_dedup_chunks_keeps_distinct_documents_and_indices():
    a = _chunk("doc1", 0)
    b = _chunk("doc2", 0)
    c = _chunk("doc1", 1)

    assert dedup_chunks([a, b, c]) == [a, b, c]


def test_dedup_chunks_handles_empty_list():
    assert dedup_chunks([]) == []
