import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.ollama_host, timeout=300.0)
    return _client


async def chat(messages: list[dict], stream: bool = False) -> dict:
    client = get_client()
    resp = await client.post(
        "/api/chat",
        json={"model": settings.ollama_model, "messages": messages, "stream": stream},
    )
    resp.raise_for_status()
    return resp.json()


async def embed(text: str) -> list[float]:
    client = get_client()
    resp = await client.post(
        "/api/embed", json={"model": settings.ollama_model, "input": text}
    )
    resp.raise_for_status()
    embeddings = resp.json().get("embeddings", [[]])
    return embeddings[0] if embeddings else []
