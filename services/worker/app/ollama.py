import httpx

from app.config import settings

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=settings.ollama_host, timeout=300.0)
    return _client


def embed(text: str) -> list[float]:
    embeddings = embed_batch([text])
    return embeddings[0] if embeddings else []


def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = get_client().post(
        "/api/embed", json={"model": settings.ollama_model, "input": texts}
    )
    resp.raise_for_status()
    return resp.json().get("embeddings", [])
