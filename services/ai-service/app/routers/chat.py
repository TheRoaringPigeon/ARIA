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


def _render_web_citation(citation: Citation) -> str:
    """Web/weather results (M10, `source_type == "web"`) have no `chunks`
    equivalent to carry their text — the snippet rides in the citation
    itself, rendered here rather than via `_render_excerpt` (which is keyed
    on `(document_id, page_number)`, meaningless for a web result).
    """
    return f"- ({citation.title}) {citation.snippet or ''}".rstrip()


def build_system_prompt(
    persona: str,
    chunks: list[retrieval.RetrievedChunk],
    entity_context: list[entity_grounding.EntityContext] | None = None,
    citation_list: list[Citation] | None = None,
    action_result: agents.ActionResult | None = None,
    has_history: bool = True,
) -> str:
    """`has_history=False` means this is the first message of a genuinely
    fresh session (nothing before it in `request.messages` — this app
    never persists chat history server-side, so a browser refresh really
    does start from zero). Grounds the model in that fact via
    `NO_HISTORY_NOTE` rather than trusting it to correctly self-report
    "I have no memory of before" on its own — a model asked point-blank
    "what were we talking about?" with truly nothing in context is prone
    to guessing an answer instead of saying so, the same failure mode
    `NO_CONTEXT_SUFFIX`/`NO_DOCUMENTS_NOTE`/`NO_ENTITY_NOTE` already exist
    to head off for missing grounding elsewhere (caught in code review,
    after this was raised as a live concern). Defaults `True` (no note) —
    only the caller building the very first turn's prompt needs to pass
    `False`; every other turn already has real prior messages in
    `wire_messages` for the model to actually reread if asked.
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

    # Retrieval and entity grounding are independent pipelines that both
    # feed this prompt — without this cross-reference, the model has no way
    # to know a cited document and a matched household record are the same
    # thing, and will hedge rather than connect them. Only document
    # citations participate — web citations (M10) have no `document_id`/
    # `page_number` to key this dict on, and aren't attached to any entity.
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


# How many prior messages `_routing_history` keeps — enough to resolve a
# short follow-up's pronouns/ellipsis (a handful of exchanges), not the
# whole transcript: this feeds a single-word routing decision and a
# tool-choice loop that both run on every turn, so every extra message
# here is tokens spent on every turn, not just the ones that need it.
_ROUTING_HISTORY_MESSAGES = 6


def _routing_history(messages: list[ChatMessage]) -> list[dict]:
    """The turns before the latest user message — given to the
    supervisor's routing classification and `research_node`'s tool-choice
    decision, both of which used to see only the current turn's `query`
    in total isolation. A short follow-up like "what about Warner
    Robins?" has no signal at all on its own; with zero history, the
    supervisor correctly (given what it was shown) fell through to
    `general`, which has no weather tools — the previous turn was a
    forecast question, but nothing carried that forward (caught in code
    review, after live use surfaced exactly this).
    """
    return [m.model_dump() for m in messages[:-1][-_ROUTING_HISTORY_MESSAGES:]]


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
    """Shared by `_stream_route_and_gather` (fresh run) and
    `_stream_resume_action` (resuming a paused one) so the two can't
    silently drift apart — same
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


# Sentinel `kind` yielded once, always last, by both `_stream_route_and_
# gather` and `_stream_resume_action` below — carries the final `_RoutingResult
# | _InterruptedResult`, never sent over the wire itself. `chat()`'s
# `_event_stream()` recognizes and strips it before continuing.
_ROUTING_RESULT_SENTINEL = "__routing_result__"

# Sentinel `kind` yielded once, always last, by `_stream_graph_steps`
# below — carries the graph's final state dict (or `None`, see that
# function's docstring). Consumed only by `_GraphStepConsumer` immediately
# below it, never sent over the wire.
_LOOP_STATE_SENTINEL = "__loop_state__"


class _GraphStepConsumer:
    """Wraps a `_stream_graph_steps` iterator so a caller can `async for`
    its frames and read `.final_state` once exhausted — both `_stream_
    route_and_gather` and `_stream_resume_action` need exactly this
    two-step unwrap (forward every frame, capture the one sentinel-carried
    final state) and had it duplicated verbatim (caught in code review).
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
        """Forwards every non-sentinel frame as-is — the two-line
        `async for kind, payload in consumer: yield (kind, payload)` both
        `_stream_route_and_gather` and `_stream_resume_action` need, kept
        here once so a future tweak (a frame-count cap, a per-frame
        timeout) is a one-place edit instead of two (caught in code
        review).
        """
        async for kind, payload in self:
            yield (kind, payload)


async def _stream_graph_steps(
    graph: CompiledStateGraph,
    graph_input: dict | Command,
    config: dict,
    *,
    recovery_context: str,
) -> AsyncIterator[tuple[str, dict | None]]:
    """Shared `graph.astream()` consumption loop for `_stream_route_and_
    gather` (fresh run) and `_stream_resume_action` (resuming a paused
    one) — extracted so the suppression/frame-construction logic can't
    silently drift between what used to be two independently-maintained
    copies of it (caught in code review). Yields `("status", ...)`/
    `("agent", ...)` SSE frame tuples as soon as each is known, then
    always ends with `(_LOOP_STATE_SENTINEL, final_state)` — `final_state`
    is `None` only when the graph produced no state at all and no
    exception either (see `_stream_resume_action`'s "already consumed"
    handling for the one case that happens in practice).

    Streams two LangGraph modes at once via `stream_mode=["values",
    "custom"]` (verified against the installed `langgraph` version before
    writing this, same practice `agents/graph.py`'s own comments already
    follow for `AsyncRedisSaver`): `"values"` is the full state snapshot
    after each step, used here only to catch `selected_agent` the instant
    `supervisor_node` finishes — well before the rest of the graph does,
    which is most of the real dead time on a Research-routed turn today.
    `"custom"` is whatever a node pushes via `get_stream_writer()`
    mid-execution (`research_node`'s tool-choice loop, `general_node`'s
    grounding gate) — surfaced here as `status` frames.

    Deliberately suppresses *all* early frames (`agent` and `status`
    alike) whenever `selected_agent == agents.INTERRUPTIBLE_AGENT` — that
    turn may end up pausing at `execute_action_node`'s interrupt, and
    `_InterruptedResult`'s whole contract (see its docstring) is that the
    turn's *only* frame is `action_proposed`, so ARIA can never leak an
    agent label before the household member has actually seen and
    confirmed the proposed action (`propose_action_node` itself never
    calls `_stream_status()` for this same reason — a status line there
    would be unreachable dead code). A non-interrupted action turn
    (propose_action_node couldn't confidently parse one) still gets its
    `agent` frame — just at the same point it always has, via
    `_routing_result_from_final_state`'s `agent_frame`, once the caller
    knows for certain there's no interrupt.

    Iterates `astream()` by hand (what `ainvoke()` does internally anyway
    — still one Redis round trip through the graph, not two) instead of
    calling `ainvoke()` directly, specifically so a late exception doesn't
    discard state a node already produced. A checkpoint write to
    `agent-store` failing *after* a node finishes (the checkpointer
    flushes in the background and can re-raise on exit) used to be
    indistinguishable from the node's own work failing — `ainvoke()` would
    raise with no way to recover the already-computed `entity_context`/
    `chunks`, forcing a caller's broad `except` to re-run its whole
    fallback from scratch, doubling core-api/Chroma/Ollama-embedding calls
    for that request (caught in code review). Re-raises only when no state
    was captured at all; `recovery_context` is folded into the warning log
    so the two callers' log lines still read distinctly.

    That reuse is only safe once the failing node's own update actually
    landed in `final_state`. If an early `agent` frame was already sent
    for a specialist whose node then raised *before* completing — as
    opposed to completing and only the checkpoint write failing
    afterward — `final_state` still lacks that specialist's `"persona"`
    key entirely (every specialist node's return dict always sets it), and
    a caller reusing it as-is would silently answer with GENERAL_PERSONA
    and no grounding under the wrong, already-displayed agent label.
    Detected here by checking for that missing key, and corrected with a
    second `agent` frame downgrading to `general` before the sentinel —
    `ChatPage.tsx`'s `onAgent` handler only bakes a label into the
    persisted message once the first token arrives, so a corrective frame
    sent here, before any Ollama call even starts, still lands in time
    (caught in code review).
    """
    suppress_live_frames: bool | None = None
    final_state: dict | None = None
    agent_frame_sent: dict | None = None
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["values", "custom"]):
            if mode == "custom":
                # `is False`, not `not suppress_live_frames` — `None` means
                # `selected_agent` isn't known yet, and must default-suppress,
                # not forward (caught in code review).
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
    """Runs the M7 agent graph to pick a specialist and gather whichever
    context it decides to fetch — yielding `(event, payload)` SSE frames
    as soon as each is known (scaling-debt.md #15's live-preview fix),
    instead of only returning a final result once the whole graph reaches
    `END`. The last item yielded is always
    `(_ROUTING_RESULT_SENTINEL, <_RoutingResult | _InterruptedResult>)`.
    Frame streaming, live-mode handling, and mid-run recovery are `_stream_
    graph_steps`'s job (see its docstring) — this function owns turning
    that into a `_RoutingResult`/`_InterruptedResult` and the outer
    degrade-to-blanket-gather fallback below.

    Degrades to the pre-M7 M4/M5 blanket behavior — `agents.
    gather_baseline_context()` (the same helper the graph's own baseline
    nodes call, so the two contracts can't silently drift apart — caught
    in code review), general persona, no `agent` frame at all — on *any*
    failure in the orchestration layer itself (graph construction, the
    Redis checkpointer being down, anything unexpected from a node),
    whether that failure happens before or after some frames were already
    yielded — a `StreamingResponse`'s 200 is already committed by the time
    any of this runs, same principle `_event_stream`'s own Ollama-call
    `except` blocks already apply one level down.
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
    """Resumes a graph run paused at `execute_action_node`'s interrupt —
    same `(event, payload)`-yielding shape as `_stream_route_and_gather`
    (see its docstring and `_stream_graph_steps`'s), for the same reason:
    an early `agent`/`status` frame must never leak before `action_
    proposed` for a still-pending action. In practice a resume rarely (if
    ever) yields anything before the final sentinel — `selected_agent` is
    already `agents.INTERRUPTIBLE_AGENT` in the checkpointed state from
    the original propose request (confirmed against the installed
    `langgraph` version: the very first `"values"` chunk of a resumed run
    already carries it), so `_stream_graph_steps` suppresses everything
    its own nodes push on the first chunk.

    `resume.thread_id` is the one narrow, single-purpose exception to M7's
    "fresh `thread_id` every request" rule (see the M8 plan's scope notes),
    reused across exactly this request and the one that proposed the
    action. The cookie is, as always, supplied fresh here and never
    checkpointed (`config["configurable"]["cookie"]`), same as every other
    node already does.

    Degrades the same way `_stream_route_and_gather` does on any
    orchestration failure — the checkpoint may have already expired
    (`agent_checkpoint_ttl_minutes`) or `agent-store` may be unreachable,
    both real possibilities in the window between the propose and confirm
    requests, not just at the very start of a turn.
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

        result = _routing_result_from_final_state(final_state, resume.thread_id)
    except Exception as exc:
        # No blanket M4/M5 grounding fallback here, unlike `_stream_route_
        # and_gather` — there's no real user question to ground on this
        # turn (a resume request carries no message history, see
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
    # Hard authorization gate (scaling-debt.md #11) — runs before anything
    # else in this handler, for both the fresh-message and resume paths
    # below, so neither ever reaches the graph/Ollama on a missing/invalid/
    # unverifiable session. Unlike the soft-degrade helpers this module used
    # to lean on, `require_chat_session` never lets a broken-auth request
    # proceed ungrounded — it either returns a real session or raises.
    try:
        session = await core_api_client.require_chat_session(session_cookie)
    except core_api_client.ChatUnauthenticated as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated") from exc
    except core_api_client.ChatAuthUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "authentication service unavailable"
        ) from exc

    await rate_limit.check_chat_rate_limit(session.user_id)

    # Every downstream consumer (Chroma-scoped retrieval, inside the graph
    # or the blanket-gather fallback) reads this from `config["configurable"]`,
    # the same never-checkpointed channel the cookie itself already travels
    # through. Always a real household id now — `require_chat_session` above
    # never lets an unauthenticated/broken-auth request reach this line.
    household_id = session.household_id

    # Stays `[]` for a resume (see `_stream_resume_action`'s docstring: its
    # `messages` is always synthetic, never real prior turns) and for the
    # degenerate "no user message at all" fallback below — `has_history`
    # further down treats a resume as never a "fresh chat" concern
    # regardless, since its synthetic turn could never ask what came
    # before it.
    history: list[dict] = []
    query: str | None = None
    if request.resume is None:
        query = _latest_user_message(request.messages)
        if query:
            history = _routing_history(request.messages)

    async def _event_stream() -> AsyncIterator[bytes]:
        # Graph execution now happens *inside* this generator (scaling-
        # debt.md #15's live-preview fix) rather than being fully awaited
        # before `StreamingResponse` is even constructed — `agent`/
        # `status` frames from `_stream_route_and_gather`/
        # `_stream_resume_action` reach the browser as soon as each is
        # known, instead of only after the whole graph reaches `END`. Both
        # helpers yield the same `(event, payload)` shape and end with the
        # `_ROUTING_RESULT_SENTINEL` sentinel carrying the final routing
        # result — never itself sent over the wire.
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
            # A single terminal frame, nothing else — no `agent`/`citations`
            # frame and no Ollama call this turn, so ARIA can never stream a
            # "done!" before the action is actually confirmed. The frontend
            # resumes with the same `thread_id` once the household member
            # clicks Confirm or Cancel (see `ChatResume`).
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
                    has_history=request.resume is not None or bool(history),
                ),
            }
        ] + wire_messages

        if (
            routing.agent_frame is not None
            and routing.agent_frame.get("name") == agents.INTERRUPTIBLE_AGENT
        ):
            # Every other agent's `agent` frame was already emitted early,
            # above, by `_stream_graph_steps` — the interruptible agent is
            # deliberately withheld until here (see its docstring), since
            # that turn might still have ended up pausing at the interrupt
            # handled above. Reaching this line means it didn't
            # (propose_action_node couldn't confidently parse one, i.e.
            # `action_result: "unclear"`), so it's safe to send now.
            yield _sse("agent", routing.agent_frame)
        yield _sse("citations", {"citations": [c.model_dump() for c in routing.citation_list]})

        stream_filter = get_adapter().create_stream_filter()
        try:
            async for chunk in ollama.chat_stream(messages):
                message = chunk.get("message") or {}
                # Verified live against the real running stack (sd15):
                # Ollama now separates a thinking-capable model's reasoning
                # into its own `message.thinking` field per chunk, rather
                # than inlining a `<think>...</think>` tag in `content` the
                # way the older Ollama server this adapter's StreamFilter
                # was originally built against did. Classifying which
                # mechanism applies is `StreamFilter`'s job (`feed_thinking`
                # vs. `feed`'s tag-sniffing), not this router's — a model/
                # server combination that inlines the tag (the StreamFilter's
                # original contract, still covered by `test_adapters.py`)
                # keeps working exactly as before, since `feed_thinking` is
                # additive to `feed`, not a replacement.
                segments = stream_filter.feed_thinking(message.get("thinking") or "")
                segments += stream_filter.feed(message.get("content") or "")
                for kind, text in segments:
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
