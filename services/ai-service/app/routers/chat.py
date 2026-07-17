import asyncio

import httpx
from aria_auth import SESSION_COOKIE_NAME
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import ValidationError

from app import citations as citations_module
from app import entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, Citation

router = APIRouter(tags=["chat"])

BASE_SYSTEM_PROMPT = (
    "You are ARIA, a household operations assistant. You help track homes, "
    "vehicles, equipment, and projects."
)

NO_CONTEXT_SUFFIX = (
    " You do not currently have any relevant household documents for this "
    "question — answer from general knowledge only, and say so if the "
    "question needs household-specific data you don't have."
)

NO_DOCUMENTS_NOTE = " No relevant household documents were found for this question."

NO_ENTITY_NOTE = (
    " No relevant household records (people, vehicles, equipment, etc.) "
    "were found for this question."
)

CONTEXT_INSTRUCTIONS = (
    " Use the following excerpts from the household's uploaded documents to "
    "answer the question if they're relevant. If they aren't relevant, "
    "answer from general knowledge instead and say so.\n\nRelevant document "
    "excerpts:\n"
)

ENTITY_CONTEXT_INSTRUCTIONS = (
    " Use the following household records — names, tags, tracked details, "
    "notes, and schedules ARIA already has on file — to answer the question "
    "if they're relevant. If they aren't relevant, answer from general "
    "knowledge instead and say so.\n\nRelevant household records:\n"
)


def _render_entity(entity: entity_grounding.EntityContext) -> str:
    header = f"- {entity.name} ({entity.domain}"
    if entity.tags:
        header += f", tags: {', '.join(entity.tags)}"
    header += ")"
    lines = [header]

    if entity.person_attrs:
        bits = [f"{label}: {value}" for label, value in entity.person_attrs.items() if value]
        if bits:
            lines.append("  " + " | ".join(bits))

    if entity.specs:
        lines.append("  Specs: " + "; ".join(f"{k}: {v}" for k, v in entity.specs.items()))

    if entity.logs:
        lines.append("  Recent logs:")
        for log in entity.logs:
            desc = f" — {log['description']}" if log.get("description") else ""
            lines.append(f"    - {log['occurred_at']} ({log['type']}): {log['title']}{desc}")

    if entity.schedules:
        lines.append("  Schedules:")
        for schedule in entity.schedules:
            due = f", next due {schedule['next_due_at']}" if schedule.get("next_due_at") else ""
            lines.append(f"    - {schedule['title']}{due}")

    return "\n".join(lines)


def _render_excerpt(chunk: retrieval.RetrievedChunk, sources: dict[tuple[str, int], str]) -> str:
    source = sources.get((chunk.mongo_document_id, chunk.page_number))
    if source is None:
        return f"- {chunk.text}"
    return f"- (Source: {source}, p.{chunk.page_number}) {chunk.text}"


def build_system_prompt(
    chunks: list[retrieval.RetrievedChunk],
    entity_context: list[entity_grounding.EntityContext] | None = None,
    citation_list: list[Citation] | None = None,
) -> str:
    entity_context = entity_context or []
    citation_list = citation_list or []
    if not chunks and not entity_context:
        return BASE_SYSTEM_PROMPT + NO_CONTEXT_SUFFIX

    sources = {(c.document_id, c.page_number): c.filename for c in citation_list}
    prompt = BASE_SYSTEM_PROMPT
    if chunks:
        excerpts = "\n\n".join(_render_excerpt(chunk, sources) for chunk in chunks)
        prompt += CONTEXT_INSTRUCTIONS + excerpts
    else:
        prompt += NO_DOCUMENTS_NOTE
    if entity_context:
        records = "\n\n".join(_render_entity(entity) for entity in entity_context)
        prompt += ENTITY_CONTEXT_INSTRUCTIONS + records
    else:
        prompt += NO_ENTITY_NOTE
    return prompt


def _latest_user_message(messages: list[ChatMessage]) -> str | None:
    return next((m.content for m in reversed(messages) if m.role == "user"), None)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> ChatResponse:
    query = _latest_user_message(request.messages)
    if query:
        entity_context_task = asyncio.create_task(
            entity_grounding.gather_entity_context(query, session_cookie)
        )
        chunks = await retrieval.retrieve_context(query)
        citation_list, entity_context = await asyncio.gather(
            citations_module.resolve_citations(session_cookie, chunks),
            entity_context_task,
        )
    else:
        chunks, entity_context, citation_list = [], [], []

    messages = [
        {"role": "system", "content": build_system_prompt(chunks, entity_context, citation_list)}
    ] + [m.model_dump() for m in request.messages]
    try:
        result = await ollama.chat(messages=messages)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="ai-service could not reach the local model"
        ) from exc

    try:
        raw_message = result["message"]
        content = get_adapter().normalize_response(raw_message["content"])
        return ChatResponse(
            message=ChatMessage(role=raw_message["role"], content=content),
            citations=citation_list,
        )
    except (KeyError, ValidationError) as exc:
        raise HTTPException(
            status_code=502,
            detail="ai-service received an unexpected response from the local model",
        ) from exc
