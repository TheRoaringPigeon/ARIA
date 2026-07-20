import chromadb
from chromadb.api import ClientAPI
from chromadb.api.async_api import AsyncClientAPI

DOCUMENTS_COLLECTION_NAME = "documents"


def build_chroma_client(*, host: str, port: int) -> ClientAPI:
    return chromadb.HttpClient(host=host, port=port)


async def build_async_chroma_client(*, host: str, port: int) -> AsyncClientAPI:
    """Same client, real async I/O instead of the sync client's blocking
    HTTP calls — for a caller on an asyncio event loop (e.g. `ai-service`'s
    per-request retrieval path) that needs a Chroma query to be genuinely
    cancellable rather than dispatched into a thread pool it can't actually
    abort once started (`asyncio.to_thread` can only stop *awaiting* a
    thread, not the thread itself — caught in code review).
    `chromadb.AsyncHttpClient` is itself a coroutine (it does an async
    handshake), unlike `HttpClient` above.
    """
    return await chromadb.AsyncHttpClient(host=host, port=port)
