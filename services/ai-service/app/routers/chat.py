import httpx
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app import ollama
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])

SYSTEM_PROMPT = (
    "You are ARIA, a household operations assistant. You help track homes, "
    "vehicles, equipment, and projects. You do not yet have access to the "
    "household's own entities, logs, or documents — answer from general "
    "knowledge only, and say so if a question needs household-specific data "
    "you don't have."
)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        m.model_dump() for m in request.messages
    ]
    try:
        result = await ollama.chat(messages=messages)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="ai-service could not reach the local model"
        ) from exc

    try:
        raw_message = result["message"]
        content = get_adapter().normalize_response(raw_message["content"])
        return ChatResponse(message=ChatMessage(role=raw_message["role"], content=content))
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=502,
            detail="ai-service received an unexpected response from the local model",
        ) from exc
