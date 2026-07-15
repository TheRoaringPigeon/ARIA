import chromadb
from chromadb.api import ClientAPI

from app.config import settings

_client: ClientAPI | None = None


def get_client() -> ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    return _client
