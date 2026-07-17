import httpx

import app.chroma as chroma_module
import app.ollama as ollama_module
from app.config import settings
from app.retrieval import retrieve_context

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

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


async def fake_embed(text):
    return [0.1, 0.2, 0.3]


async def test_retrieve_context_returns_chunks(monkeypatch):
    fake_collection = FakeCollection(result=CANNED_RESULT)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    monkeypatch.setattr(chroma_module, "get_documents_collection", lambda: fake_collection)

    chunks = await retrieve_context("what's the oil capacity")

    assert len(chunks) == 2
    assert chunks[0].text == "The oil capacity is 5 quarts."
    assert chunks[0].mongo_document_id == "doc1"
    assert chunks[0].page_number == 4
    assert chunks[0].chunk_index == 0
    assert chunks[0].section_header == "Maintenance"
    assert chunks[0].distance == 0.12
    assert chunks[1].section_header is None
    assert fake_collection.calls[0]["n_results"] == settings.rag_top_k


async def test_retrieve_context_respects_custom_top_k(monkeypatch):
    fake_collection = FakeCollection(result=CANNED_RESULT)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    monkeypatch.setattr(chroma_module, "get_documents_collection", lambda: fake_collection)
    monkeypatch.setattr(settings, "rag_top_k", 7)

    await retrieve_context("what's the oil capacity")

    assert fake_collection.calls[0]["n_results"] == 7


async def test_retrieve_context_degrades_on_chroma_error(monkeypatch):
    fake_collection = FakeCollection(exc=RuntimeError("chroma is down"))
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    monkeypatch.setattr(chroma_module, "get_documents_collection", lambda: fake_collection)

    assert await retrieve_context("anything") == []


async def test_retrieve_context_degrades_on_embed_error(monkeypatch):
    async def failing_embed(text):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama_module, "embed", failing_embed)

    assert await retrieve_context("anything") == []


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
    monkeypatch.setattr(chroma_module, "get_documents_collection", lambda: fake_collection)

    chunks = await retrieve_context("what's the oil capacity")

    assert len(chunks) == 1
    assert chunks[0].text == "The oil capacity is 5 quarts."


async def test_retrieve_context_handles_empty_collection(monkeypatch):
    empty_result = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    fake_collection = FakeCollection(result=empty_result)
    monkeypatch.setattr(ollama_module, "embed", fake_embed)
    monkeypatch.setattr(chroma_module, "get_documents_collection", lambda: fake_collection)

    assert await retrieve_context("anything") == []
