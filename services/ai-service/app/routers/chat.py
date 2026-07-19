import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
from aria_auth import SESSION_COOKIE_NAME
from fastapi import APIRouter, Cookie
from fastapi.responses import StreamingResponse

from app import agents
from app import entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, Citation

logger = logging.getLogger(__name__)

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
    persona: str,
    chunks: list[retrieval.RetrievedChunk],
    entity_context: list[entity_grounding.EntityContext] | None = None,
    citation_list: list[Citation] | None = None,
) -> str:
    entity_context = entity_context or []
    citation_list = citation_list or []
    if not chunks and not entity_context:
        return persona + NO_CONTEXT_SUFFIX

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

    prompt = persona
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


@dataclass
class _RoutingResult:
    agent_frame: dict | None
    persona: str
    entity_context: list[entity_grounding.EntityContext]
    chunks: list[retrieval.RetrievedChunk]
    citation_list: list[Citation]


async def _route_and_gather(query: str, cookie: str | None) -> _RoutingResult:
    """Runs the M7 agent graph to pick a specialist and gather whichever
    context it decides to fetch. Degrades to the pre-M7 M4/M5 blanket
    behavior — `agents.gather_baseline_context()` (the same helper the
    graph's own baseline nodes call, so the two contracts can't silently
    drift apart — caught in code review), general persona, no `agent`
    frame at all — on *any* failure in the orchestration layer itself
    (graph construction, the Redis checkpointer being down, anything
    unexpected from a node). This is a new failure axis M7 introduces on
    top of the M4/M5 grounding calls (which already degrade internally to
    `[]` and can't raise here) — same strict-decoupling contract as
    everything else in this pipeline: a broken enhancement layer falls
    back to the last-known-good behavior, it never breaks the request.
    """
    try:
        graph = await agents.get_graph()
        config = {"configurable": {"cookie": cookie, "thread_id": str(uuid4())}}

        # Iterates `astream(..., stream_mode="values")` by hand (what
        # `ainvoke()` does internally anyway — still one Redis round trip
        # through the graph, not two) instead of calling `ainvoke()`
        # directly, specifically so a late exception doesn't discard state
        # a node already produced. A checkpoint write to `agent-store`
        # failing *after* a node finishes (the checkpointer flushes in the
        # background and can re-raise on exit) used to be indistinguishable
        # from the node's own work failing — `ainvoke()` would raise with
        # no way to recover the already-computed `entity_context`/`chunks`,
        # so the broad `except` below would re-run the entire blanket
        # gather from scratch, doubling core-api/Chroma/Ollama-embedding
        # calls for that request (caught in code review).
        final_state: dict | None = None
        try:
            async for state_update in graph.astream(
                {"query": query}, config, stream_mode="values"
            ):
                final_state = state_update
        except Exception:
            if final_state is None:
                raise
            logger.warning(
                "agent graph raised after already producing a final state "
                "(likely a checkpoint write failure) — using the "
                "already-computed result instead of re-gathering from scratch",
                exc_info=True,
            )

        selected_agent = final_state.get("selected_agent")
        agent_frame = None
        if selected_agent is not None:
            agent_frame = {
                "name": selected_agent,
                # `.get(selected_agent, selected_agent)`, not `[selected_agent]`
                # — if `VALID_AGENTS`/`AGENT_LABELS` (app/agents/state.py,
                # kept in sync by an assertion there) ever desync, this must
                # never raise: an unlabeled agent name is a fine degraded
                # display, but a `KeyError` here would get caught by the
                # broad `except` below and misreported as "agent
                # orchestration unavailable", masking a real code bug as an
                # infra failure (caught in code review).
                "label": agents.AGENT_LABELS.get(selected_agent, selected_agent),
            }
        return _RoutingResult(
            agent_frame=agent_frame,
            persona=final_state.get("persona", agents.GENERAL_PERSONA),
            entity_context=final_state.get("entity_context", []),
            chunks=final_state.get("chunks", []),
            citation_list=final_state.get("citation_list", []),
        )
    except Exception as exc:
        # Broad on purpose — matches the degrade-don't-fail contract every
        # other grounding path in this codebase already uses (entity_
        # grounding.py, retrieval.py, citations.py all catch this broadly
        # too) — but a bare "unavailable" message made a real bug (a
        # `TypeError` inside a node) indistinguishable from "Redis is
        # down" in production logs. Naming the exception type doesn't
        # narrow what's caught, just what's easy to grep for afterward.
        logger.warning(
            "agent orchestration unavailable (%s: %s), degrading to blanket M4/M5 grounding",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        entity_context, chunks, citation_list = await agents.gather_baseline_context(
            query, cookie
        )
        return _RoutingResult(
            agent_frame=None,
            persona=agents.GENERAL_PERSONA,
            entity_context=entity_context,
            chunks=chunks,
            citation_list=citation_list,
        )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> StreamingResponse:
    query = _latest_user_message(request.messages)
    if query:
        routing = await _route_and_gather(query, session_cookie)
    else:
        routing = _RoutingResult(
            agent_frame=None,
            persona=agents.GENERAL_PERSONA,
            entity_context=[],
            chunks=[],
            citation_list=[],
        )

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                routing.persona, routing.chunks, routing.entity_context, routing.citation_list
            ),
        }
    ] + [m.model_dump() for m in request.messages]

    async def _event_stream() -> AsyncIterator[bytes]:
        if routing.agent_frame is not None:
            yield _sse("agent", routing.agent_frame)
        yield _sse("citations", {"citations": [c.model_dump() for c in routing.citation_list]})

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
