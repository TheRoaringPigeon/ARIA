import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
from aria_auth import SESSION_COOKIE_NAME
from fastapi import APIRouter, Cookie
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app import agents
from app import entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, ChatResume, Citation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Human-readable label for the `action_proposed` SSE frame's `label` field —
# `tool` carries the raw tool name (`create_log`/`create_schedule`) for any
# programmatic consumer; `label` is what the confirmation card shows.
_ACTION_TOOL_LABELS: dict[str, str] = {
    "create_log": "Log an entry",
    "create_schedule": "Create a schedule",
}

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


def _render_action_result_note(action_result: agents.ActionResult) -> str:
    """The trailing note `build_system_prompt()` appends when a write-path
    turn (M8) just resolved — same shape as `NO_CONTEXT_SUFFIX`/
    `NO_DOCUMENTS_NOTE`/`NO_ENTITY_NOTE` above, telling the model what
    actually happened so it can acknowledge it naturally instead of
    guessing (or, worse, claiming success that didn't happen).
    """
    status = action_result.get("status")
    if status == "done":
        return (
            f" You just completed this action for the household: "
            f"{action_result.get('summary')}. Acknowledge it naturally, in "
            f"past tense — do not claim any other action happened."
        )
    if status == "cancelled":
        return (
            f" The user just cancelled this proposed action: "
            f"{action_result.get('summary')}. Acknowledge the cancellation "
            f"naturally — do not claim anything was done."
        )
    if status == "failed":
        return (
            f" You just tried to complete an action for the household but it "
            f"failed: {action_result.get('detail')}. Tell the user it didn't "
            f"go through and why, in plain language — do not claim it "
            f"succeeded."
        )
    # "unclear" — propose_action_node couldn't confidently parse a specific
    # action out of the user's message.
    return (
        " You couldn't confidently determine a specific action (log or "
        "schedule) from the user's message. Ask a clarifying question about "
        "what they'd like you to log or schedule, using whatever household "
        "context below is relevant — do not claim anything was done."
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
    action_result: agents.ActionResult | None = None,
) -> str:
    entity_context = entity_context or []
    citation_list = citation_list or []
    if not chunks and not entity_context:
        prompt = persona + NO_CONTEXT_SUFFIX
        if action_result is not None:
            prompt += _render_action_result_note(action_result)
        return prompt

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
    if action_result is not None:
        prompt += _render_action_result_note(action_result)
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
    # M8 write path (see the plan's design decisions) — the completed-turn
    # note `build_system_prompt()` appends, set only when a resumed run
    # reached `execute_action_node`. A run that instead paused at
    # `execute_action_node`'s interrupt never produces a `_RoutingResult` at
    # all — see `_InterruptedResult` below.
    action_result: agents.ActionResult | None = None


@dataclass
class _InterruptedResult:
    """The graph run paused at `execute_action_node`'s interrupt rather than
    reaching `END` — a distinct type from `_RoutingResult`, not a handful
    of always-present-but-usually-`None` fields bolted onto it (an earlier
    version did that; caught in code review as a source of "read
    `.thread_id`/`.proposed_action` on an ordinary, non-interrupted turn"
    bugs nothing would catch until runtime). `chat()` short-circuits to a
    single `action_proposed` frame for this case, never reaching
    `build_system_prompt`/Ollama at all this turn.
    """

    thread_id: str
    proposed_action: agents.ProposedAction


def _routing_result_from_final_state(
    final_state: dict, thread_id: str
) -> "_RoutingResult | _InterruptedResult":
    """Shared by `_route_and_gather` (fresh run) and `_resume_action`
    (resuming a paused one) so the two can't silently drift apart — same
    reasoning as `gather_baseline_context` being one implementation for two
    callers. `"__interrupt__"` being present in a `stream_mode="values"`
    drained state is exactly how a paused-at-`interrupt()` run is told apart
    from one that reached `END` (verified against the installed `langgraph`
    version before writing this — see the M8 plan's sequencing).
    """
    if "__interrupt__" in final_state:
        return _InterruptedResult(
            thread_id=thread_id,
            proposed_action=final_state.get("proposed_action") or {},
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
        action_result=final_state.get("action_result"),
    )


async def _route_and_gather(query: str, cookie: str | None) -> "_RoutingResult | _InterruptedResult":
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
        thread_id = str(uuid4())
        config = {"configurable": {"cookie": cookie, "thread_id": thread_id}}

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

        return _routing_result_from_final_state(final_state, thread_id)
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


async def _resume_action(resume: ChatResume, cookie: str | None) -> "_RoutingResult | _InterruptedResult":
    """Resumes a graph run paused at `execute_action_node`'s interrupt —
    `resume.thread_id` is the one narrow, single-purpose exception to M7's
    "fresh `thread_id` every request" rule (see the M8 plan's scope notes),
    reused across exactly this request and the one that proposed the
    action. The cookie is, as always, supplied fresh here and never
    checkpointed (`config["configurable"]["cookie"]`), same as every other
    node already does.

    Degrades the same way `_route_and_gather` does on any orchestration
    failure — the checkpoint may have already expired
    (`agent_checkpoint_ttl_minutes`) or `agent-store` may be unreachable,
    both real possibilities in the window between the propose and confirm
    requests, not just at the very start of a turn.
    """
    try:
        graph = await agents.get_graph()
        config = {"configurable": {"cookie": cookie, "thread_id": resume.thread_id}}

        final_state: dict | None = None
        try:
            async for state_update in graph.astream(
                Command(resume=resume.decision), config, stream_mode="values"
            ):
                final_state = state_update
        except Exception:
            if final_state is None:
                raise
            logger.warning(
                "agent graph raised after already producing a final state "
                "while resuming an action — using the already-computed "
                "result instead of re-running from scratch",
                exc_info=True,
            )

        if final_state is None:
            # A thread whose interrupt was already consumed (e.g. the
            # client retried this exact confirm/cancel after its SSE
            # stream dropped, but the earlier resume had already run the
            # graph to completion) makes `astream()` yield no state at all
            # and no exception either — LangGraph's checkpoint for this
            # thread has no pending task left to resume. Recover the
            # already-checkpointed outcome instead of falling into the
            # `except` below and telling the household member the action
            # failed when it likely already succeeded (caught in code
            # review).
            snapshot = await graph.aget_state(config)
            if not snapshot.values:
                raise RuntimeError(f"no checkpointed state for thread {resume.thread_id!r}")
            final_state = snapshot.values

        return _routing_result_from_final_state(final_state, resume.thread_id)
    except Exception as exc:
        # No blanket M4/M5 grounding fallback here, unlike `_route_and_
        # gather` — there's no real user question to ground on this turn
        # (a resume request carries no message history, see
        # `schemas/chat.py`'s `ChatRequest.messages`), so there's nothing
        # meaningful to search or match entities against. Degrades to an
        # ungrounded answer that explicitly says the confirm/cancel didn't
        # go through — not a bare `action_result=None` "no context" reply,
        # which read back to the household member as an ordinary,
        # successful turn with zero indication the action's fate is
        # actually unknown (whether it was ever written is genuinely
        # unrecoverable once the checkpoint is gone; caught in code
        # review).
        logger.warning(
            "resuming a proposed action failed (%s: %s), degrading to a failure acknowledgment",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return _RoutingResult(
            agent_frame=None,
            persona=agents.GENERAL_PERSONA,
            entity_context=[],
            chunks=[],
            citation_list=[],
            action_result={
                "status": "failed",
                "detail": (
                    "ARIA lost track of this request (it may have expired) — "
                    "please try again"
                ),
            },
        )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> StreamingResponse:
    if request.resume is not None:
        routing = await _resume_action(request.resume, session_cookie)
    else:
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

    if isinstance(routing, _InterruptedResult):
        # A single terminal frame, nothing else — no `agent`/`citations`
        # frame and no Ollama call this turn, so ARIA can never stream a
        # "done!" before the action is actually confirmed. The frontend
        # resumes with the same `thread_id` once the household member
        # clicks Confirm or Cancel (see `ChatResume`).
        async def _interrupt_stream() -> AsyncIterator[bytes]:
            tool = routing.proposed_action.get("tool")
            yield _sse(
                "action_proposed",
                {
                    "thread_id": routing.thread_id,
                    "tool": tool,
                    "label": _ACTION_TOOL_LABELS.get(tool, "Proposed action"),
                    "summary": routing.proposed_action.get("summary"),
                },
            )

        return StreamingResponse(_interrupt_stream(), media_type="text/event-stream")

    if request.resume is not None:
        # A resume request always carries `messages: []` (there's no real
        # history to resend — the graph's own state is already
        # checkpointed under `resume.thread_id`, see `ChatResume`). But
        # sending Ollama a messages list with nothing but a system prompt
        # is its own problem: most chat templates expect at least one user
        # turn to respond to, and an earlier version sent none at all here
        # — never actually exercised against a real model, only against
        # tests that mock `chat_stream` entirely (caught in code review). A
        # short, synthetic user turn naming what the household member just
        # did gives the model something concrete to respond to, without
        # resurrecting real cross-turn conversation memory — the actual
        # outcome still comes from `action_result`'s note below, this is
        # only here so the model has *a* turn to answer.
        wire_messages = [
            {
                "role": "user",
                "content": (
                    "I confirmed the proposed action."
                    if request.resume.decision == "confirm"
                    else "I cancelled the proposed action."
                ),
            }
        ]
    else:
        wire_messages = [m.model_dump() for m in request.messages]

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                routing.persona,
                routing.chunks,
                routing.entity_context,
                routing.citation_list,
                routing.action_result,
            ),
        }
    ] + wire_messages

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
