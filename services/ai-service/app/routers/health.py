import asyncio

from fastapi import APIRouter

from app.chroma import get_client
from app.ollama import get_client as get_ollama_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    try:
        client = get_client()
        await asyncio.to_thread(client.heartbeat)
        chroma_status = "ok"
    except Exception as exc:  # noqa: BLE001 — surfaced directly in the response
        chroma_status = f"error: {exc}"

    try:
        resp = await get_ollama_client().get("/api/tags")
        resp.raise_for_status()
        ollama_status = "ok"
    except Exception as exc:  # noqa: BLE001 — surfaced directly in the response
        ollama_status = f"error: {exc}"

    return {
        "service": "ai-service",
        "status": "ok",
        "chroma": chroma_status,
        "ollama": ollama_status,
    }
