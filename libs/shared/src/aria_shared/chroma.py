import chromadb
from chromadb.api import ClientAPI

DOCUMENTS_COLLECTION_NAME = "documents"


def build_chroma_client(*, host: str, port: int) -> ClientAPI:
    return chromadb.HttpClient(host=host, port=port)
