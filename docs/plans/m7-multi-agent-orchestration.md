# M7 — AI Phase 5: Multi-Agent Orchestration: Implementation Plan

## Context

M1–M6 are done — the MVP defined in `docs/roadmap.md` is complete. Today's
`/chat` (`ai-service/app/routers/chat.py::chat()`) is one flat pipeline for
every query: run `entity_grounding.gather_entity_context()` and
`retrieval.retrieve_context()` unconditionally and in parallel, resolve
citations, build one system prompt (`build_system_prompt()`), stream one
model completion back. There is no notion of intent, specialization, or
multi-step reasoning — every question gets the same blanket context,
regardless of whether it's about a vehicle, a document, or neither.

M7 implements PRD Phase 5: *"Deployment of specialized agents (e.g.,
Maintenance Agent, Vehicle Specialist, and Research Assistant) coordinating
through stateful runtimes."* The backend stack in the PRD names LangGraph
explicitly; it is not yet a dependency anywhere in this repo (confirmed —
`services/ai-service/pyproject.toml`'s dependency list doesn't include it,
though its `description` string already namedrops "LangGraph agents" as
aspirational text). This is the first milestone that actually adds it.

**Scope was narrowed deliberately, confirmed with the user up front, before
writing this plan:**
1. **Agents are read-only this milestone.** `ai-service` has zero write
   capability against `core-api` today (`core_api_client.py` only has
   `list_entities`/`list_entity_logs`/`list_entity_schedules`/
   `get_document` — all `GET`). Giving an LLM-driven agent the ability to
   create/update/delete household records is a real safety surface
   (entity deletion already cascades hard-deletes to logs/schedules per
   `data-model.md`), and the PRD itself sequences that concern into
   **Phase 6** ("MCP integration... safe agent execution"). Building
   write-capable tools now would mean inventing confirmation/guardrail UX
   with no PRD phase actually asking for it yet — deferred on purpose.
2. **Checkpointing is Redis-backed** — originally scoped as "a new logical
   DB on the `redis` service `core-api`/`worker` already run for Celery,
   not new infrastructure." **Revised during implementation**: live
   verification against that plain `redis:7-alpine` instance failed
   (`unknown command 'FT._LIST'`) — `langgraph-checkpoint-redis` requires
   the RediSearch/RedisJSON modules for its checkpoint indices, which
   stock Redis doesn't ship. Rather than upgrade the Celery-critical
   shared instance to Redis Stack (real risk to core-api/worker's queue
   for an AI-layer feature), `ai-service` gets its own small, dedicated
   `agent-store` service (`redis/redis-stack-server`) in docker-compose —
   one new container, scoped to this one feature, zero blast radius on
   Celery if it's ever down. This is a better fit for strict decoupling
   than the original "reuse the shared instance" framing turned out to
   be, even though it does mean one new piece of infrastructure rather
   than zero. `ai-service`'s "no Mongo connection of its own" invariant
   (`docs/architecture.md`) is untouched either way.
3. **The frontend keeps resending the full message list every request** —
   no `conversation_id`, no change to that contract. This means the
   Redis checkpointer's practical benefit *this milestone* is real but
   modest: it makes the graph a genuinely stateful runtime per LangGraph's
   own execution model (every node transition is checkpointed under a
   `thread_id`), and sets up cleanly for Phase 6's likely next use of
   checkpointing (human-in-the-loop interrupts before a write-capable tool
   runs) — but it does **not** yet give ARIA cross-turn memory beyond what
   resending full history already provides. Said plainly here so this
   milestone doesn't get credited with a capability it doesn't yet have.

---

## Key design decisions

**Tools wrap the existing M4/M5 pipelines verbatim — no new fine-grained
tool surface.** The obvious "textbook" design would give each specialist
granular CRUD-shaped tools (`list_entities(domain)`, `get_entity(id)`,
`list_schedules(id)`, ...), letting the model compose multi-step lookups
itself. That's real scope: new `core_api_client.py` functions, a JSON tool
schema per function, and — most importantly — it would require rebuilding
`build_system_prompt()`'s rendering logic, which today expects the
pre-shaped `EntityContext`/`RetrievedChunk` objects `entity_grounding.py`
and `retrieval.py` already produce. Instead, this milestone gives
specialists exactly two coarse tools, each a direct wrapper:
- `gather_household_context` → `entity_grounding.gather_entity_context(query, cookie)`, unchanged.
- `search_household_documents` → `retrieval.retrieve_context(query)`, unchanged.

This means `build_system_prompt(persona, chunks, entity_context,
citation_list)` needs exactly **one** change from today (a new leading
`persona` parameter) — everything else about prompt construction,
citation cross-referencing, and the "no relevant X" fallback text is
reused as-is. Fine-grained, composable tools are a legitimate fast-follow
once this coarser routing layer is proven live, not a gap in this plan.

**Only the Research Assistant gets an iterative tool-use loop — the other
three specialists call their one tool unconditionally.** Maintenance Agent
and Vehicle Specialist each have exactly one available tool
(`gather_household_context`) with no arguments to choose — there's no
real decision for the model to make, so making it "decide" whether to call
its only lever adds an LLM round-trip for zero behavioral value. Those two
nodes just call the tool directly, differing from each other (and from
today's behavior) purely in **persona** — the system-prompt framing
(vehicle-service-interval language vs. general household-upkeep language).
General (the fallback when the supervisor can't confidently classify)
calls **both** tools unconditionally, via `asyncio.gather` — this is
deliberately identical to today's exact M4/M5 blanket behavior, so an
ambiguous query regresses to nothing worse than what M6 already shipped.
Research Assistant is the one place a bounded, genuine tool-choice loop
earns its keep: deciding *whether* to search, optionally *reformulating*
the query, and *whether to search again* after seeing the first result is
real, useful agentic behavior for a document-research persona — capped at
`AI_SERVICE_AGENT_MAX_TOOL_CALLS` (default `2`) iterations.

**Revised after code review, post-ship (2026-07-18): Maintenance Agent and
Vehicle Specialist gained `search_household_documents` too, and Research
Assistant gained baseline `gather_household_context`.** The curated-
tool-subset design above shipped as written, but review caught it as a
real, unacknowledged capability regression, not just a stylistic
narrowing: pre-M7, *every* query always searched both documents and
household entities. A vehicle-classified question about an uploaded
owner's manual ("what's the oil capacity, per the manual?") would now
find nothing and cite nothing — the manual was simply never searched —
and a research-classified question about a document linked to a specific
household entity could never surface that link (`build_system_prompt()`'s
citation-to-entity cross-referencing needs `entity_context` populated,
and Research Assistant hard-coded it to `[]`). Fixed by giving all four
specialists the same baseline gather (`_gather_household_and_documents()`
in `nodes.py`, `asyncio.gather`-ing both tools, same shape as the old
`general_node`) — `maintenance_node`/`vehicle_node`/`general_node` are now
thin wrappers around one shared function differing only in `persona`,
and `research_node` additionally kicks off baseline entity-grounding as a
concurrent task (overlapped with its tool-choice loop, no added latency)
on top of its still-unique iterative document-search loop. Genuine
specialization is now exactly two things: persona framing (always) and
Research Assistant's extra iterative search (still unique to it) — not
which tools are reachable at all. See `nodes.py`'s module comments for
the corrected reasoning.

**The tool-choice loop is a plain bounded Python `for` loop inside one node
function, not graph-level cyclic edges.** LangGraph's textbook ReAct
pattern models each tool-call round as its own graph superstep with a
conditional edge back to the calling node. That's the right choice for an
open-ended agent loop; it's overkill for "ask the model up to twice
whether it wants to search again." A single node function containing a
bounded loop is simpler, easier to unit test in isolation, and is still
fully checkpointed as one atomic state transition — consistent with this
codebase's habit of picking the simplest thing that satisfies the
requirement over the maximal "proper" version of a pattern (see M6's
`StreamFilter` being a hand-rolled state machine rather than a pulled-in
parser library).

**No native Ollama tool-calling (`tools` param on `/api/chat`) — a
hand-rolled JSON-in-content-string protocol instead.** Researched before
committing to this: Ollama's native `tools` parameter has a documented,
open bug for Qwen3 specifically (ollama/ollama#14601) — tool definitions
get serialized as malformed Go-struct text instead of valid JSON when
`tools` is passed, and prior tool calls get silently stripped from resent
conversation history. Rather than build against a known-broken path (or a
runtime feature-detection dance), every orchestration call in this
milestone — the supervisor's classification and the Research Assistant's
tool-decision — is a **plain content-only completion** the calling code
parses itself:
- Supervisor: system prompt instructs *"respond with exactly one word:
  maintenance, vehicle, research, or general."* Response is passed through
  `get_adapter().normalize_response()` (strips a `<think>...</think>`
  block — Qwen3 prefixes essentially every reply with one, including
  short classification replies) then matched case-insensitively against
  the four labels; no match of any kind (empty, garbled, a full sentence
  that doesn't contain one of the words) falls back to `"general"` —
  degrade-don't-fail, consistent with every other grounding path in this
  codebase.
- Research Assistant: system prompt instructs a strict JSON reply shape —
  `{"tool": "search_household_documents", "query": "..."}` or `{"tool":
  null}`. Same `normalize_response()` strip, then `json.loads` wrapped in
  a broad `try/except` defaulting to `{"tool": None}` on any parse
  failure (missing tool, unparseable JSON, unexpected shape) — a
  confused model just means the loop ends early and synthesis proceeds
  with whatever was already gathered, never a 500.

This sidesteps the specific Qwen3/Ollama bug entirely (never touches the
`tools` param) and reuses `normalize_response()`, which M6 orphaned —
it's declared on `ModelAdapter` and unit-tested but nothing in `app/`
called it after the streaming rewrite. This milestone gives it a real
caller again.

**One new SSE event, `agent` — emitted once, before `citations`.** Mirrors
exactly how M6 added `thinking`/`token`/`error` to the existing `citations`
frame: extend the vocabulary, don't fork the endpoint. `event: agent\ndata:
{"name": "vehicle", "label": "Vehicle Specialist"}\n\n`, sent as soon as
the supervisor's routing decision is known — before the chosen specialist
even runs its tool(s) — so the frontend can show "Vehicle Specialist is
looking into this…" during what's otherwise a silent gap (an entity-lookup
or document-search round trip) today's UI has no visibility into. `name`
is the internal routing key (`maintenance`/`vehicle`/`research`/`general`),
`label` is the display string, both sourced from one shared
`AGENT_LABELS` dict so the frontend never hardcodes the mapping.

**Degrades to today's exact M5-era behavior if the graph/Redis layer
fails — a new failure axis this milestone introduces, handled the same
way every other optional-context failure already is in this codebase.**
Unlike `retrieval.py`/`entity_grounding.py`/`citations.py` (each degrades
internally, independently, to `[]`), the *graph itself* is new
infrastructure with a new dependency (Redis) that could be down. Wrapping
the graph invocation in a broad `try/except` and falling back to calling
`entity_grounding.gather_entity_context()` + `retrieval.retrieve_context()`
directly (today's exact M4/M5 code path, persona = the unchanged
`BASE_SYSTEM_PROMPT` text, no `agent` SSE frame emitted at all) means a
Redis outage degrades chat back to "M6, no agents" rather than breaking it
— the same strict-decoupling contract every other piece of this pipeline
already honors. This is verified live in the walkthrough below by
stopping `redis` mid-session, matching how M4/M5/M6 each verified their
own degrade paths against the real stack.

**Graph state carries dataclass instances directly (`EntityContext`,
`RetrievedChunk`), not hand-converted dicts.** LangGraph's default
checkpoint serializer (`JsonPlusSerde`) handles dataclasses natively: this
avoids a manual to-dict/from-dict layer that would otherwise need to exist
solely for the Redis round trip. Verified empirically during
implementation (see sequencing step 3) rather than assumed — if it turns
out not to round-trip cleanly through the installed `langgraph-checkpoint`
version, falling back to `dataclasses.asdict()`/reconstructing on read is
a contained, one-file fix.

**`AsyncRedisSaver` directly, for both `.setup()` and runtime — verified
against the installed version, not assumed.** `langgraph-checkpoint-redis`
has a documented open issue where `AsyncRedisSaver.setup()` calls an async
index-creation method without awaiting it (langchain-ai/langgraph#5472);
the maintainers' own suggested workaround was to use the sync `RedisSaver`
for setup instead. Checked directly against what actually resolved into
`uv.lock` (`langgraph-checkpoint-redis==0.5.1`) via `inspect.getsource()`
before writing `graph.py`: `AsyncRedisSaver.setup()` already properly
`await`s `self.asetup()` in this version — the bug is fixed upstream, so
the sync-saver workaround this plan originally called for turned out to
be unnecessary. `graph.py` uses `AsyncRedisSaver(redis_url=...)` directly,
one `await checkpointer.setup()` call at graph-construction time. Flagged
and checked explicitly rather than assumed, same spirit as M6 catching
the "blank line arrives as its own delta" surprise by testing against the
real stack instead of trusting the first draft — and the same live check
is what surfaced the RediSearch/`agent-store` requirement above.

**No frontend `conversation_id` — `thread_id` is a fresh UUID generated by
the router on every request.** Per the scope decision above. This still
gives the compiled graph a real, distinct checkpoint namespace per
request (LangGraph's `config={"configurable": {"thread_id": ...}}`
mechanism), it just doesn't span multiple HTTP requests — a deliberate,
honest scoping choice, not an oversight. `docs/roadmap.md`'s "explicitly
out of scope" list calls this out directly.

---

## File-by-file plan

### `services/ai-service/pyproject.toml`
- Add `"langgraph>=0.2"` and `"langgraph-checkpoint-redis>=0.1"` to
  `dependencies`. Regenerate the workspace `uv.lock`.

### `services/ai-service/app/config.py`
- Add `redis_url: str = "redis://agent-store:6379/0"` — points at the
  dedicated `agent-store` Redis Stack instance (see the docker-compose
  section below), not the plain `redis` service Celery uses — the
  checkpointer needs RediSearch/RedisJSON, which stock Redis doesn't have.
- Add `agent_max_tool_calls: int = 2`.

### `services/ai-service/app/ollama.py`
- Add `async def complete(messages: list[dict]) -> str:` — one-shot,
  non-streaming `/api/chat` call (`"stream": False`), returns
  `resp.json()["message"]["content"]`. This revives the shape of the
  non-streaming `chat()` function M6 deleted as dead code (confirmed:
  today's `ollama.py` only has `chat_stream()` and `embed()`) — named
  `complete()` rather than `chat()` both to avoid confusion with that
  removed name and because "complete" signals its role here: an internal,
  one-shot orchestration call, not a user-facing chat turn.
- `chat_stream()` and `embed()`: unchanged.

### `services/ai-service/app/agents/__init__.py` (new package)
- Empty, or a thin re-export of `get_graph` for a slightly shorter import
  path from `routers/chat.py` — matches `app/adapters/__init__.py`'s
  existing `get_adapter()` re-export pattern.

### `services/ai-service/app/agents/state.py` (new)
- `AgentState(TypedDict)`: `query: str`, plus `NotRequired[...]` fields
  populated by whichever node runs — `selected_agent: str`,
  `persona: str`, `entity_context: list[entity_grounding.EntityContext]`,
  `chunks: list[retrieval.RetrievedChunk]`, `tool_calls_made: list[str]`
  (names of tools actually invoked this run — surfaced for the optional
  `tool_call` SSE frames described below, and useful for debugging/test
  assertions).
- `AGENT_LABELS: dict[str, str]` = `{"maintenance": "Maintenance Agent",
  "vehicle": "Vehicle Specialist", "research": "Research Assistant",
  "general": "ARIA"}` — the single source of truth for both the `agent`
  SSE frame's `label` field and any future frontend display logic.
- Persona strings: `MAINTENANCE_PERSONA`, `VEHICLE_PERSONA`,
  `RESEARCH_PERSONA` (each a short paragraph extending
  `routers/chat.py`'s existing `BASE_SYSTEM_PROMPT` wording with
  domain-appropriate framing — vehicle framing mentions mileage/service
  intervals, research framing emphasizes citing sources), and
  `GENERAL_PERSONA = BASE_SYSTEM_PROMPT` (imported from `routers/chat.py`,
  unchanged) — kept as a named alias specifically so it's obvious General
  is meant to be behaviorally identical to pre-M7 chat, not an oversight
  that it looks the same as the others minus specialization.

### `services/ai-service/app/agents/nodes.py` (new)
- `async def supervisor_node(state: AgentState, config: RunnableConfig) -> dict`:
  builds the one-word classification prompt, calls `ollama.complete()`,
  strips via `get_adapter().normalize_response()`, matches
  case-insensitively against the four labels (substring match against the
  trimmed/lowered response, not exact-equality — a model that answers
  "vehicle." or "Vehicle" or "I'd say vehicle" should still route
  correctly), falls back to `"general"` on no match. Returns
  `{"selected_agent": label}`.
- `async def maintenance_node(state, config) -> dict`: calls
  `entity_grounding.gather_entity_context(state["query"],
  config["configurable"]["cookie"])`. Returns `{"entity_context": ...,
  "chunks": [], "persona": MAINTENANCE_PERSONA, "tool_calls_made":
  ["gather_household_context"]}`.
- `async def vehicle_node(state, config) -> dict`: identical body to
  `maintenance_node`, `persona=VEHICLE_PERSONA`. (Deliberately duplicated
  rather than parameterized — two four-line functions differing only in
  one constant is clearer than a shared helper taking a persona argument
  for what's this small; revisit if a third near-identical specialist
  shows up.)
- `async def general_node(state, config) -> dict`: `asyncio.gather()`s
  both `entity_grounding.gather_entity_context(...)` and
  `retrieval.retrieve_context(state["query"])` — the exact concurrent
  shape `routers/chat.py::chat()` uses today. `persona=GENERAL_PERSONA`,
  `tool_calls_made=["gather_household_context", "search_household_documents"]`.
- `async def research_node(state, config) -> dict`: bounded loop, `for _
  in range(settings.agent_max_tool_calls)`: build a tool-decision prompt
  (system message describing the one available tool + a running
  scratchpad summarizing prior search results this iteration, e.g. "you
  already searched for 'X' and found N excerpts"), `ollama.complete()`,
  normalize, `json.loads` in a `try/except` defaulting to `{"tool":
  None}`; if `tool` is falsy, `break`; otherwise call
  `retrieval.retrieve_context(parsed.get("query") or state["query"])`,
  extend an accumulating `chunks` list, append to the scratchpad and
  `tool_calls_made`, loop again. Returns `{"chunks": accumulated,
  "entity_context": [], "persona": RESEARCH_PERSONA, "tool_calls_made": [...]}`.

### `services/ai-service/app/agents/graph.py` (new)
- `_build_graph()`: constructs `AsyncRedisSaver(redis_url=settings.redis_url)`
  and `await`s `.setup()` on it directly (see the design decision above —
  verified against the installed version, the historical sync-workaround
  bug is already fixed). `StateGraph(AgentState)`: `add_node` for all five node functions,
  `set_entry_point("supervisor")`, `add_conditional_edges("supervisor",
  lambda s: s["selected_agent"], {"maintenance": "maintenance", "vehicle":
  "vehicle", "research": "research", "general": "general"})`, each
  specialist node → `END`. `.compile(checkpointer=...)`.
- Module-level `_graph: CompiledGraph | None = None` + `async def
  get_graph() -> CompiledGraph:` lazy singleton — mirrors
  `chroma.py::get_documents_collection()` and `ollama.py::get_client()`'s
  existing lazy-singleton pattern exactly. Built once, reused across
  requests (the compiled graph object itself holds no per-request state —
  everything request-scoped flows through `config`/`state`, not closures).

### `services/ai-service/app/routers/chat.py`
- `build_system_prompt()`: gains a new leading `persona: str` parameter,
  replacing the hardcoded reference to `BASE_SYSTEM_PROMPT` at the top of
  the function body with the passed-in `persona`. `BASE_SYSTEM_PROMPT`
  constant itself stays (now imported by `agents/state.py` as
  `GENERAL_PERSONA`'s value) — every other line of this function is
  unchanged: excerpt rendering, entity rendering, citation
  cross-referencing, the `NO_CONTEXT_SUFFIX`/`NO_DOCUMENTS_NOTE`/
  `NO_ENTITY_NOTE` fallbacks.
- `chat()`: replace the current unconditional
  `entity_grounding.gather_entity_context()` + `retrieval.retrieve_context()`
  block with:
  - `thread_id = str(uuid4())`, `config = {"configurable": {"cookie":
    session_cookie, "thread_id": thread_id}}`.
  - `try:` run the graph: `graph = await agents.get_graph()`; iterate
    `async for update in graph.astream({"query": query}, config,
    stream_mode="updates"):` — on the first update containing
    `selected_agent` (i.e. the supervisor's), capture
    `agent_frame = {"name": name, "label": AGENT_LABELS[name]}` for
    emission (see below); after the loop completes, `final_state = (await
    graph.aget_state(config)).values` and pull `persona`, `entity_context`,
    `chunks` from it.
  - `except Exception:` (Redis down, graph construction failure, anything
    unexpected from the orchestration layer) — `logger.warning(...,
    exc_info=True)`, fall back to today's exact blanket calls
    (`entity_grounding.gather_entity_context()` +
    `retrieval.retrieve_context()` directly, `persona =
    agents.state.GENERAL_PERSONA`), and `agent_frame = None` (no `agent`
    SSE event emitted at all in this fallback path — an absent frame is
    indistinguishable from pre-M7 chat, which is exactly the point).
  - `citation_list = await citations_module.resolve_citations(session_cookie, chunks)` —
    unchanged call, now fed by whichever chunks the graph (or the
    fallback) actually gathered.
  - `messages = [{"role": "system", "content": build_system_prompt(persona,
    chunks, entity_context, citation_list)}] + [...]` — same shape as
    today, one new argument.
  - `_event_stream()`: **unchanged**, except it yields the `agent` SSE
    frame first (only if `agent_frame is not None`) before the existing
    `citations` frame. The `stream_filter`/`ollama.chat_stream()`/error-
    handling body is untouched — this is the milestone's biggest
    risk-reduction point: the proven M6 streaming path for the actual
    answer text is not touched at all, only what feeds its system prompt
    changes.
- No query (`query is None`, same as today's early-exit case): skip the
  graph entirely, `persona = GENERAL_PERSONA`, `chunks = []`,
  `entity_context = []`, `agent_frame = None` — matches today's existing
  short-circuit exactly.

### `services/ai-service/app/adapters/base.py`, `qwen.py`
- No changes. `normalize_response()` gets a real caller again (from
  `agents/nodes.py`) but its implementation is untouched.

### `docker-compose.yml`
- New `agent-store` service: `redis/redis-stack-server:latest`, same
  `redis-cli ping` healthcheck shape as the existing `redis` service. Not
  a swap of the existing `redis` service — a separate one, scoped to
  `ai-service` only (see the design-decision revision above for why: the
  checkpointer needs RediSearch/RedisJSON, which plain Redis doesn't
  have, and upgrading Celery's shared instance for an AI-layer feature
  would widen this milestone's blast radius past `ai-service`).
- `ai-service` service: add `AI_SERVICE_REDIS_URL:
  redis://agent-store:6379/0` to its `environment` block, and add
  `agent-store: condition: service_healthy` to its `depends_on` block.
  No other service's config changes — `redis`/Celery are untouched.

### `services/ai-service/tests/test_ollama.py`
- New cases for `complete()`: a fake non-streaming JSON response body
  returns its `message.content`; an HTTP error status raises before
  returning — following the existing `FakeAsyncClient`/monkeypatch
  convention already used for `chat_stream()`'s tests in this file.

### `services/ai-service/tests/test_agents.py` (new)
- Node-level unit tests, each following the established
  hand-rolled-fake + `monkeypatch.setattr(<module>, "<name>", <fake>)`
  convention (no `httpx.MockTransport`, no `unittest.mock`):
  - `supervisor_node`: fake `ollama.complete` returning each of the four
    labels in various casings/punctuation → correct routing; a garbled/
    empty/off-topic response → falls back to `"general"`.
  - `maintenance_node`/`vehicle_node`: fake
    `entity_grounding.gather_entity_context` returning canned
    `EntityContext` objects → returned state has the right `persona` and
    `entity_context`, empty `chunks`.
  - `general_node`: fakes for both underlying calls → both invoked
    (assert via a `calls` list the fakes append to, the existing idiom for
    "was this called" without a real mock framework), state has both
    `entity_context` and `chunks` populated.
  - `research_node`: fake `ollama.complete` that always returns a
    "search again" JSON payload → loop runs exactly
    `agent_max_tool_calls` times, never more, even though the fake would
    keep saying yes forever; a fake that returns `{"tool": null}`
    immediately → loop exits after one call with zero chunks gathered;
    malformed JSON on the first call → same degrade-to-zero-chunks
    outcome, no exception propagates.
- One graph-level integration test using LangGraph's in-memory
  `MemorySaver` (not Redis — keeps this test file dependency-free, same
  reasoning as every other `ai-service` test avoiding real Chroma/Ollama):
  build the graph with `MemorySaver` instead of the Redis checkpointer,
  monkeypatch the same node-level dependencies as above, `ainvoke()` for
  a representative query per specialist, assert final state's
  `selected_agent`/`persona` match expectations end-to-end through the
  conditional-edge routing.

### `services/ai-service/tests/test_chat.py`
- Existing monkeypatches of `entity_grounding_module.gather_entity_context`
  and `retrieval_module.retrieve_context` still work unchanged — those
  functions are still exactly what gets called, just now from inside node
  functions instead of directly from the router. New/changed cases:
  - New: `ollama_module.complete` monkeypatched to return `"vehicle"` →
    an `agent` SSE frame (`{"name": "vehicle", "label": "Vehicle
    Specialist"}`) appears first, before `citations`, followed by the
    existing `thinking`/`token` sequence unchanged.
  - New: `ollama_module.complete` returning garbage → routes to
    `"general"`, behaves identically to a pre-M7 request (this is the
    regression check that matters most — every existing M3–M6 test case
    in this file should keep passing once `general` is wired as the
    no-`agent`-frame-visible-difference default... actually the `agent`
    frame *is* emitted for `general` too, since routing always resolves to
    *some* label; only the graph-unavailable fallback path omits it
    entirely — see next case).
  - New: monkeypatch `agents_module.get_graph` to raise (simulating Redis
    down) → falls back to the direct `entity_grounding`/`retrieval` calls,
    **no** `agent` frame in the SSE output, `citations`/`thinking`/`token`
    otherwise unchanged from today's M5/M6 behavior — the strict-
    decoupling regression check.
  - Existing no-context/retrieved-chunks/citations-present/think-block
    cases: unaffected, still pass with the graph now sitting in front of
    the same underlying calls.

### `services/frontend/src/api/chat.ts`
- `streamChatMessage`'s `handlers` parameter gains an optional
  `onAgent?: (agent: { name: string; label: string }) => void`. The SSE
  frame parser dispatches an `event: agent` frame to it the same way
  `citations`/`thinking`/`token` are already dispatched. New exported type
  `ChatAgent = { name: string; label: string }`.

### `services/frontend/src/hooks/useStreamChatMessage.ts`
- `send(messages, callbacks)`'s `callbacks` shape gains `onAgent`,
  threaded straight through to `api.streamChatMessage(...)` — no new
  local state in the hook itself (same as `onCitations`/`onThinking`
  today).

### `services/frontend/src/pages/ChatPage.tsx`
- `DisplayMessage` gains `agentLabel?: string`.
- New `pendingAgentRef = useRef<ChatAgent | null>(null)` — same pattern as
  the existing `pendingCitations` local variable inside `send()`, since
  the `agent` frame (like `citations`) always arrives before the
  placeholder message exists.
- `onAgent`: stash into `pendingAgentRef.current`.
- `onToken`'s first-call placeholder creation: `{role: 'assistant',
  content: delta, citations: pendingCitations, agentLabel:
  pendingAgentRef.current?.label}` — attached once, same lifecycle as
  citations.
- The "Thinking…" placeholder block: shows `pendingAgentRef.current?.label`
  prefixed (e.g. *"Vehicle Specialist is looking into this…"*) once the
  `agent` frame has arrived but before any `thinking`/`token` content has,
  falling back to today's plain `"Thinking…"` if no agent frame arrived
  yet (or never will, in the graph-degraded fallback case) — reuses the
  exact slot `thinkingPreview` already renders into, no new UI region.
- `pendingAgentRef` reset to `null` at the start of every `send()`, same
  as `pendingCitations`.

### `services/frontend/src/components/ChatBubble.tsx`
- Renders `message.agentLabel` (when present) as a small subtle caption
  above the assistant bubble's content — same visual weight as the
  existing "Sources" pill row, not competing with it. Absent entirely
  for user messages and for any assistant message with no `agentLabel`
  (the graph-degraded fallback case) — no placeholder text, the caption
  simply doesn't render, so a degraded response looks exactly like a
  pre-M7 one.

---

## Sequencing

1. `ollama.py::complete()` + its `test_ollama.py` cases — small, isolated,
   no LangGraph involved yet.
2. `agents/state.py` + `agents/nodes.py` + `test_agents.py`'s node-level
   cases (using fakes, no real Redis/Ollama needed). **Before writing
   `graph.py`**, verify live against the running stack that `qwen3:14b`
   reliably produces the requested one-word classification and small JSON
   blob when instructed via plain system-prompt text (not Ollama's native
   `tools` param, which this design deliberately avoids) — this is the one
   genuinely open empirical question in the whole plan, worth confirming
   before building the graph and tests on top of an unverified assumption.
3. `agents/graph.py`, verified against a real `docker compose up agent-store`:
   confirm `AsyncRedisSaver.setup()` succeeds and a checkpoint containing a
   dataclass (`EntityContext`/`RetrievedChunk`) round-trips correctly —
   adjust the dataclass-vs-dict serialization design decision above if
   this surfaces a problem. (This is exactly the step that first surfaced
   the RediSearch/`agent-store` requirement — plain `redis:7-alpine`
   failed outright with `unknown command 'FT._LIST'` before the dedicated
   Redis Stack service existed.) `test_agents.py`'s `MemorySaver`-based
   integration test.
4. `routers/chat.py` rewrite (`build_system_prompt()`'s new `persona`
   param, the graph-invocation-with-fallback block, the `agent` SSE
   frame) + rewritten `test_chat.py`. Verify against the running
   docker-compose stack with `curl -N` against `/chat` directly (same
   technique M6 used) for at least one query per specialist — confirm the
   `agent` frame's `name` matches the question's actual domain, and that
   `citations`/`thinking`/`token` still arrive correctly afterward.
5. `frontend`: `api/chat.ts`, `useStreamChatMessage.ts`, `ChatPage.tsx`,
   `ChatBubble.tsx`. Confirm `npm run lint`/`npm run build` clean.
6. Manual browser walkthrough end-to-end (below).

---

## Verification

**Manual walkthrough (happy path):** log in, open Chat.
- Ask a vehicle-specific question (e.g. about the household's tracked
  Sienna/Ranger, using M5's seeded data if still present) — confirm the
  "Vehicle Specialist is looking into this…" preview appears, the
  finished bubble is captioned "Vehicle Specialist," and the answer
  reflects the vehicle's actual tracked records (same grounding quality
  as M4's household-grounding fast-follow, just now routed through a
  specialist instead of blanket context).
- Ask a question that should hit an uploaded document (M5's citation test
  data) — confirm it routes to "Research Assistant," citations still
  appear as a "Sources" pill row exactly as M5 built, and the answer
  quotes/reflects the actual document content.
- Ask a deliberately ambiguous or off-topic question — confirm it falls
  back to "general" framing and produces an answer no worse than M5/M6
  would have (both entity grounding and document retrieval still run, per
  the General node's design).
- Ask a follow-up in the same conversation — confirm the resent request
  (full history, `{role, content}` only, unchanged wire contract) still
  succeeds and the supervisor re-routes independently per-message (no
  "sticky" specialist across turns — each request is classified fresh,
  since there's no cross-turn memory this milestone).

**Degrade-path walkthrough:** stop the `agent-store` container mid-session
(not `redis` — that one's Celery's, untouched by this milestone), ask
another question — confirm no `agent` caption appears (looks exactly like
pre-M7 chat), but the answer is still grounded via the direct
`entity_grounding`/`retrieval` fallback and streams normally; restart
`agent-store`, confirm agent routing resumes with no `ai-service` restart
needed (mirrors every prior milestone's decoupling verification).

**Then the unchanged M4/M5/M6 strict-decoupling checks**, now exercised
through the graph instead of directly: stop `chromadb` → Research
Assistant's `search_household_documents` tool degrades to `[]` internally
(already true of `retrieval.retrieve_context()`, untouched by this
milestone) rather than failing the whole graph run; stop `core-api` →
Maintenance/Vehicle/General's `gather_household_context` tool degrades to
`[]` the same way.

**Automated:** `uv run pytest` in `ai-service` (extended `test_ollama.py`,
new `test_agents.py`, rewritten `test_chat.py`); `npm run lint` + `npm run
build` in `frontend`. As with every prior AI milestone, there's no
frontend unit test runner in this project — the new `agent` caption/
preview-label UI is verified by the manual walkthrough only.

### Critical files
- `services/ai-service/app/agents/state.py`
- `services/ai-service/app/agents/nodes.py`
- `services/ai-service/app/agents/graph.py`
- `services/ai-service/app/ollama.py`
- `services/ai-service/app/routers/chat.py`
- `services/ai-service/app/config.py`
- `docker-compose.yml`
- `services/frontend/src/api/chat.ts`
- `services/frontend/src/pages/ChatPage.tsx`
- `services/frontend/src/components/ChatBubble.tsx`
