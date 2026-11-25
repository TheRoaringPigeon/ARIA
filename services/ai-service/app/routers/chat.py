import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
from aria_auth import SESSION_COOKIE_NAME
from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app import agents, rate_limit
from app import core_api_client, entity_grounding, ollama, retrieval
from app.adapters import get_adapter
from app.schemas.chat import ChatMessage, ChatRequest, ChatResume, Citation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_ACTION_TOOL_LABELS: dict[str, str] = {
    "create_log": "Log an entry",
    "create_schedule": "Create a schedule",
}

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

NO_HISTORY_NOTE = (
    " This is the very first message of a brand-new conversation — this "
    "app doesn't save chat history across a page refresh, so there is no "
    "earlier conversation to reference. If the user asks what you "
    "discussed before, or refers to a previous exchange, say plainly that "
    "this is a fresh conversation with nothing before it, rather than "
    "guessing or inventing one."
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

WEB_RESULTS_INSTRUCTIONS = (
    " Use the following web search/weather results, gathered just now by "
    "the Research Assistant, to answer the question if they're relevant.\n"
    "\nRecent web results:\n"
)


def _render_action_result_note(action_result: agents.ActionResult) -> str:
    """Tells the model what the write path actually did, so it never claims
    a write that didn't happen.
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

    if entity.attrs:
        bits = [f"{label}: {value}" for label, value in entity.attrs.items() if value]
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


def _render_web_citation(citation: Citation) -> str:
    """A web or weather result, whose text rides in the citation itself."""
    return f"- ({citation.title}) {citation.snippet or ''}".rstrip()


def build_system_prompt(
    persona: str,
    chunks: list[retrieval.RetrievedChunk],
    entity_context: list[entity_grounding.EntityContext] | None = None,
    citation_list: list[Citation] | None = None,
    action_result: agents.ActionResult | None = None,
    has_history: bool = True,
) -> str:
    """The answer's system prompt: persona, grounding, notes for missing
    grounding, and the action result. `has_history=False` adds
    `NO_HISTORY_NOTE` so the model says a fresh chat has no past instead
    of inventing one.
    """
    entity_context = entity_context or []
    citation_list = citation_list or []
    document_citations = [c for c in citation_list if c.source_type == "document"]
    web_citations = [c for c in citation_list if c.source_type == "web"]
    if not chunks and not entity_context and not web_citations:
        prompt = persona + NO_CONTEXT_SUFFIX
        if not has_history:
            prompt += NO_HISTORY_NOTE
        if action_result is not None:
            prompt += _render_action_result_note(action_result)
        return prompt

    entity_names_by_id = {entity.id: entity.name for entity in entity_context}
    sources: dict[tuple[str, int], _Source] = {}
    entity_documents: dict[str, list[str]] = {}
    for citation in document_citations:
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
    if web_citations:
        results = "\n\n".join(_render_web_citation(c) for c in web_citations)
        prompt += WEB_RESULTS_INSTRUCTIONS + results
    if not has_history:
        prompt += NO_HISTORY_NOTE
    if action_result is not None:
        prompt += _render_action_result_note(action_result)
    return prompt


def _latest_user_message(messages: list[ChatMessage]) -> str | None:
    return next((m.content for m in reversed(messages) if m.role == "user"), None)


_ROUTING_HISTORY_MESSAGES = 6


def _routing_history(messages: list[ChatMessage]) -> list[dict]:
    """The last few turns before the latest message, for routing and tool
    choice, so a follow-up like "what about Warner Robins?" keeps its topic.
    """
    return [m.model_dump() for m in messages[:-1][-_ROUTING_HISTORY_MESSAGES:]]


@dataclass
class _RoutingResult:
    agent_frame: dict | None
    persona: str
    entity_context: list[entity_grounding.EntityContext]
    chunks: list[retrieval.RetrievedChunk]
    citation_list: list[Citation]
    action_result: agents.ActionResult | None = None


@dataclass
class _InterruptedResult:
    """A run paused at `execute_action_node`'s interrupt. The turn answers
    with a single `action_proposed` frame and no model call.
    """

    thread_id: str
    proposed_action: agents.ProposedAction


def _routing_result_from_final_state(
    final_state: dict, thread_id: str
) -> "_RoutingResult | _InterruptedResult":
    """Turns a drained run's final state into a routing or interrupted
    result; `__interrupt__` in the state marks a paused run.
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


_ROUTING_RESULT_SENTINEL = "__routing_result__"

_LOOP_STATE_SENTINEL = "__loop_state__"


class _GraphStepConsumer:
    """Forwards `_stream_graph_steps` frames and keeps the final state for
    after the loop.
    """

    def __init__(self, steps: AsyncIterator[tuple[str, dict | None]]) -> None:
        self._steps = steps
        self.final_state: dict | None = None

    def __aiter__(self) -> "_GraphStepConsumer":
        return self

    async def __anext__(self) -> tuple[str, dict]:
        kind, payload = await self._steps.__anext__()
        if kind == _LOOP_STATE_SENTINEL:
            self.final_state = payload
            raise StopAsyncIteration
        return kind, payload

    async def forward(self) -> AsyncIterator[tuple[str, dict]]:
        """Yields every frame except the final-state sentinel."""
        async for kind, payload in self:
            yield (kind, payload)


async def _stream_graph_steps(
    graph: CompiledStateGraph,
    graph_input: dict | Command,
    config: dict,
    *,
    recovery_context: str,
) -> AsyncIterator[tuple[str, dict | None]]:
    """Runs the graph, yielding `agent` and `status` frames as they're known,
    then the final state. Early frames are withheld on action turns. A
    late failure keeps state already produced, and a specialist that
    failed mid-node is corrected with an `agent` frame for `general`.
    """
    suppress_live_frames: bool | None = None
    final_state: dict | None = None
    agent_frame_sent: dict | None = None
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["values", "custom"]):
            if mode == "custom":
                if suppress_live_frames is False:
                    status = chunk.get("status")
                    if status:
                        yield ("status", {"content": status})
                continue
            final_state = chunk
            if suppress_live_frames is None and "selected_agent" in chunk:
                selected_agent = chunk["selected_agent"]
                suppress_live_frames = selected_agent == agents.INTERRUPTIBLE_AGENT
                if not suppress_live_frames:
                    agent_frame_sent = {
                        "name": selected_agent,
                        "label": agents.AGENT_LABELS.get(selected_agent, selected_agent),
                    }
                    yield ("agent", agent_frame_sent)
    except Exception:
        if final_state is None:
            raise
        logger.warning(
            "agent graph raised after already producing a final state %s — "
            "using the already-computed result instead of re-gathering from scratch",
            recovery_context,
            exc_info=True,
        )

    if agent_frame_sent is not None and final_state is not None and "persona" not in final_state:
        yield ("agent", {"name": "general", "label": agents.AGENT_LABELS["general"]})

    yield (_LOOP_STATE_SENTINEL, final_state)


async def _stream_route_and_gather(
    query: str, history: list[dict], cookie: str | None, household_id: str | None
) -> AsyncIterator[tuple[str, dict | _RoutingResult | _InterruptedResult]]:
    """Routes and gathers context through the graph, streaming frames, and
    ends with the routing result. Any orchestration failure, before or
    after frames went out, falls back to the baseline gather.
    """
    try:
        graph = await agents.get_graph()
        thread_id = str(uuid4())
        config = {"configurable": {"cookie": cookie, "household_id": household_id, "thread_id": thread_id}}

        consumer = _GraphStepConsumer(
            _stream_graph_steps(
                graph,
                {"query": query, "history": history},
                config,
                recovery_context="(likely a checkpoint write failure)",
            )
        )
        async for kind, payload in consumer.forward():
            yield (kind, payload)

        result = _routing_result_from_final_state(consumer.final_state, thread_id)
    except Exception as exc:
        logger.warning(
            "agent orchestration unavailable (%s: %s), degrading to blanket M4/M5 grounding",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        entity_context, chunks, citation_list = await agents.gather_baseline_context(
            query, cookie, household_id
        )
        result = _RoutingResult(
            agent_frame=None,
            persona=agents.GENERAL_PERSONA,
            entity_context=entity_context,
            chunks=chunks,
            citation_list=citation_list,
        )
    yield (_ROUTING_RESULT_SENTINEL, result)


async def _stream_resume_action(
    resume: ChatResume, cookie: str | None, household_id: str | None
) -> AsyncIterator[tuple[str, dict | _RoutingResult | _InterruptedResult]]:
    """Resumes a run paused at the action interrupt, reusing its thread_id
    with a fresh cookie. Falls back like `_stream_route_and_gather` when
    the checkpoint expired or the store is down.
    """
    try:
        graph = await agents.get_graph()
        config = {
            "configurable": {"cookie": cookie, "household_id": household_id, "thread_id": resume.thread_id}
        }

        consumer = _GraphStepConsumer(
            _stream_graph_steps(
                graph,
                Command(resume=resume.decision),
                config,
                recovery_context="while resuming an action",
            )
        )
        async for kind, payload in consumer.forward():
            yield (kind, payload)

        final_state = consumer.final_state
        if final_state is None:
            snapshot = await graph.aget_state(config)
            if not snapshot.values:
                raise RuntimeError(f"no checkpointed state for thread {resume.thread_id!r}")
            final_state = snapshot.values

        result = _routing_result_from_final_state(final_state, resume.thread_id)
    except Exception as exc:
        logger.warning(
            "resuming a proposed action failed (%s: %s), degrading to a failure acknowledgment",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        result = _RoutingResult(
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
    yield (_ROUTING_RESULT_SENTINEL, result)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> StreamingResponse:
    try:
        session = await core_api_client.require_chat_session(session_cookie)
    except core_api_client.ChatUnauthenticated as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated") from exc
    except core_api_client.ChatAuthUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "authentication service unavailable"
        ) from exc

    await rate_limit.check_chat_rate_limit(session.user_id)

    household_id = session.household_id

    history: list[dict] = []
    query: str | None = None
    if request.resume is None:
        query = _latest_user_message(request.messages)
        if query:
            history = _routing_history(request.messages)

    async def _event_stream() -> AsyncIterator[bytes]:
        routing: _RoutingResult | _InterruptedResult
        if request.resume is not None:
            stream = _stream_resume_action(request.resume, session_cookie, household_id)
        elif query:
            stream = _stream_route_and_gather(query, history, session_cookie, household_id)
        else:
            stream = None
            routing = _RoutingResult(
                agent_frame=None,
                persona=agents.GENERAL_PERSONA,
                entity_context=[],
                chunks=[],
                citation_list=[],
            )

        if stream is not None:
            async for kind, payload in stream:
                if kind == _ROUTING_RESULT_SENTINEL:
                    routing = payload
                    break
                yield _sse(kind, payload)

        if isinstance(routing, _InterruptedResult):
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
            return

        if request.resume is not None:
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
                    has_history=request.resume is not None or bool(history),
                ),
            }
        ] + wire_messages

        if (
            routing.agent_frame is not None
            and routing.agent_frame.get("name") == agents.INTERRUPTIBLE_AGENT
        ):
            yield _sse("agent", routing.agent_frame)
        yield _sse("citations", {"citations": [c.model_dump() for c in routing.citation_list]})

        stream_filter = get_adapter().create_stream_filter()
        try:
            async for chunk in ollama.chat_stream(messages, think=request.resume is None):
                message = chunk.get("message") or {}
                segments = stream_filter.feed_thinking(message.get("thinking") or "")
                segments += stream_filter.feed(message.get("content") or "")
                for kind, text in segments:
                    yield _sse(_EVENT_NAME_BY_KIND.get(kind, kind), {"content": text})
            for kind, text in stream_filter.flush():
                yield _sse(_EVENT_NAME_BY_KIND.get(kind, kind), {"content": text})
        except httpx.HTTPError:
            yield _sse("error", {"detail": "ai-service could not reach the local model"})
        except (json.JSONDecodeError, AttributeError, KeyError):
            yield _sse(
                "error",
                {"detail": "ai-service received an unexpected response from the local model"},
            )

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
