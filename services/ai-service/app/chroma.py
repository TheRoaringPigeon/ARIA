from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from app.config import settings
from aria_shared.chroma import DOCUMENTS_COLLECTION_NAME, build_chroma_client

_client: ClientAPI | None = None


def get_client() -> ClientAPI:
    global _client
    if _client is None:
        _client = build_chroma_client(host=settings.chroma_host, port=settings.chroma_port)
    return _client


def get_documents_collection() -> Collection:
    return get_client().get_or_create_collection(DOCUMENTS_COLLECTION_NAME)
