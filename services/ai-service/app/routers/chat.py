import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from aria_auth import SESSION_COOKIE_NAME
from fastapi import APIRouter, Cookie
from fastapi.responses import StreamingResponse

from app import citations as citations_module
from app import entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, Citation

router = APIRouter(tags=["chat"])

# Maps a StreamFilter chunk's classification to the SSE event name it's
# sent under — "answer" content is what the UI treats as the real,
# permanent message, so it goes out as "token" (matching the wire name the
# frontend already expects a chat completion delta to have). Looked up with
# .get(kind, kind) below, not [kind] — a StreamChunkKind this dict doesn't
# know about (e.g. from a future adapter) falls back to its own name as the
# event name instead of crashing the generator.
_EVENT_NAME_BY_KIND = {"thinking": "thinking", "answer": "token"}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


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


@dataclass
class _Source:
    filename: str
    linked_entity_names: list[str]


def _render_entity(
    entity: entity_grounding.EntityContext,
    entity_documents: dict[str, list[str]] | None = None,
) -> str:
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

    documents = (entity_documents or {}).get(entity.id)
    if documents:
        lines.append("  Documents on file: " + ", ".join(documents))

    return "\n".join(lines)


def _render_excerpt(chunk: retrieval.RetrievedChunk, sources: dict[tuple[str, int], _Source]) -> str:
    source = sources.get((chunk.mongo_document_id, chunk.page_number))
    if source is None:
        return f"- {chunk.text}"
    if source.linked_entity_names:
        linked = "; linked to: " + ", ".join(source.linked_entity_names)
    else:
        linked = ""
    return f"- (Source: {source.filename}, p.{chunk.page_number}{linked}) {chunk.text}"


def build_system_prompt(
    chunks: list[retrieval.RetrievedChunk],
    entity_context: list[entity_grounding.EntityContext] | None = None,
    citation_list: list[Citation] | None = None,
) -> str:
    entity_context = entity_context or []
    citation_list = citation_list or []
    if not chunks and not entity_context:
        return BASE_SYSTEM_PROMPT + NO_CONTEXT_SUFFIX

    # Retrieval and entity grounding are independent pipelines that both
    # feed this prompt — without this cross-reference, the model has no way
    # to know a cited document and a matched household record are the same
    # thing, and will hedge rather than connect them.
    entity_names_by_id = {entity.id: entity.name for entity in entity_context}
    sources: dict[tuple[str, int], _Source] = {}
    entity_documents: dict[str, list[str]] = {}
    for citation in citation_list:
        linked_entity_names = [
            entity_names_by_id[entity_id]
            for entity_id in dict.fromkeys(citation.entity_ids)
            if entity_id in entity_names_by_id
        ]
        sources[(citation.document_id, citation.page_number)] = _Source(
            filename=citation.filename, linked_entity_names=linked_entity_names
        )
        for entity_id in citation.entity_ids:
            if entity_id not in entity_names_by_id:
                continue
            filenames = entity_documents.setdefault(entity_id, [])
            if citation.filename not in filenames:
                filenames.append(citation.filename)

    prompt = BASE_SYSTEM_PROMPT
    if chunks:
        excerpts = "\n\n".join(_render_excerpt(chunk, sources) for chunk in chunks)
        prompt += CONTEXT_INSTRUCTIONS + excerpts
    else:
        prompt += NO_DOCUMENTS_NOTE
    if entity_context:
        records = "\n\n".join(_render_entity(entity, entity_documents) for entity in entity_context)
        prompt += ENTITY_CONTEXT_INSTRUCTIONS + records
    else:
        prompt += NO_ENTITY_NOTE
    return prompt


def _latest_user_message(messages: list[ChatMessage]) -> str | None:
    return next((m.content for m in reversed(messages) if m.role == "user"), None)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> StreamingResponse:
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

    async def _event_stream() -> AsyncIterator[bytes]:
        yield _sse("citations", {"citations": [c.model_dump() for c in citation_list]})

        stream_filter = get_adapter().create_stream_filter()
        try:
            async for chunk in ollama.chat_stream(messages):
                delta = (chunk.get("message") or {}).get("content", "")
                for kind, text in stream_filter.feed(delta):
                    yield _sse(_EVENT_NAME_BY_KIND.get(kind, kind), {"content": text})
            for kind, text in stream_filter.flush():
                yield _sse(_EVENT_NAME_BY_KIND.get(kind, kind), {"content": text})
        except httpx.HTTPError:
            # Headers (200) are already committed by the time this
            # generator runs — a downstream failure can only be reported
            # as an in-stream event, not an HTTP status code.
            yield _sse("error", {"detail": "ai-service could not reach the local model"})
        except (json.JSONDecodeError, AttributeError, KeyError):
            # Same reasoning as above, but for a malformed/unexpected
            # response shape from the local model rather than a transport
            # failure — the old non-streaming endpoint caught this as a 502
            # via (KeyError, ValidationError); there's no status code to
            # use here, so it's the same in-stream "error" event.
            yield _sse(
                "error",
                {"detail": "ai-service received an unexpected response from the local model"},
            )

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
