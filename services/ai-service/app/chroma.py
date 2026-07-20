from chromadb.api import ClientAPI
from chromadb.api.async_api import AsyncClientAPI
from chromadb.api.models.AsyncCollection import AsyncCollection
from chromadb.api.models.Collection import Collection

from app.config import settings
from app.lazy_singleton import AsyncLazySingleton
from aria_shared.chroma import (
    DOCUMENTS_COLLECTION_NAME,
    build_async_chroma_client,
    build_chroma_client,
)

_client: ClientAPI | None = None


def get_client() -> ClientAPI:
    global _client
    if _client is None:
        _client = build_chroma_client(host=settings.chroma_host, port=settings.chroma_port)
    return _client


def get_documents_collection() -> Collection:
    return get_client().get_or_create_collection(DOCUMENTS_COLLECTION_NAME)


# The sync client/collection above stay in use for `routers/health.py`'s
# simple, low-frequency heartbeat check — only `retrieval.py`'s per-request
# query path (on the hot `/chat` path, and the one a confident write-path
# decision may cancel mid-flight) needs the real async client.
_async_client: AsyncLazySingleton[AsyncClientAPI] = AsyncLazySingleton(
    lambda: build_async_chroma_client(host=settings.chroma_host, port=settings.chroma_port)
)


async def get_async_client() -> AsyncClientAPI:
    return await _async_client.get()


async def _build_documents_collection_async() -> AsyncCollection:
    client = await get_async_client()
    return await client.get_or_create_collection(DOCUMENTS_COLLECTION_NAME)


# `get_or_create_collection` is a real Chroma network round trip, not a
# local lookup — cached the same way `_async_client` is so `retrieval.py`'s
# hot `/chat` query path (and `research_node`'s tool loop, which can call
# it more than once per turn) isn't re-resolving the same collection handle
# over the wire on every single call (caught in code review).
_documents_collection_async: AsyncLazySingleton[AsyncCollection] = AsyncLazySingleton(
    _build_documents_collection_async
)


async def get_documents_collection_async() -> AsyncCollection:
    return await _documents_collection_async.get()
