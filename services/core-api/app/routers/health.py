from fastapi import APIRouter

from app.db import get_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    try:
        await get_client().admin.command("ping")
        mongo_status = "ok"
    except Exception as exc:  # noqa: BLE001 — surfaced directly in the response
        mongo_status = f"error: {exc}"

    return {"service": "core-api", "status": "ok", "mongo": mongo_status}
