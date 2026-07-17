import json
from collections.abc import AsyncIterator

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.ollama_host, timeout=300.0)
    return _client


async def chat_stream(messages: list[dict]) -> AsyncIterator[dict]:
    """Yields Ollama's own NDJSON stream frames (one per content delta) as
    parsed dicts — `/api/chat` with `"stream": true` returns newline-
    delimited JSON, not SSE. Raises before yielding anything if the initial
    connection/response fails.
    """
    client = get_client()
    async with client.stream(
        "POST",
        "/api/chat",
        json={"model": settings.ollama_model, "messages": messages, "stream": True},
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            yield json.loads(line)


async def embed(text: str) -> list[float]:
    client = get_client()
    resp = await client.post(
        "/api/embed", json={"model": settings.embed_model, "input": text}
    )
    resp.raise_for_status()
    embeddings = resp.json().get("embeddings", [[]])
    return embeddings[0] if embeddings else []
