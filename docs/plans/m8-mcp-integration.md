# M8 — AI Phase 6: MCP Integration: Implementation Plan

> **Post-implementation note (code review):** three details below drifted
> from what actually shipped. (1) `confirm_gate_node` was merged into
> `execute_action_node` — the graph is `propose_action_node` →
> `execute_action_node` → `END`, and `execute_action_node` calls
> `interrupt()` itself rather than reading `state["decision"]` (that state
> field no longer exists). (2) The supervisor's classifier word for the
> action/write category is `"log_or_schedule"`, not `"action"` — the
> internal agent name is still `"action"`, but the word the model is asked
> to answer with was changed to avoid a false-positive collision with
> ordinary prose containing "action". (3) Scope item 1's "no core-api
> changes at all" turned out not to hold — `services/core-api/app/schemas/
> {logs,schedules}.py` moved to `libs/shared/src/aria_shared/schemas/`, and
> `services/core-api/app/routers/{logs,schedules}.py`'s imports changed to
> match. The rest of this document (design rationale, sequencing) still
> reflects the actual implementation; only these three specifics changed.

## Context

M1–M7 are done — the MVP plus multi-agent orchestration (`docs/roadmap.md`)
are complete. Every agent capability so far (M7) is **read-only**:
`ai-service`'s LangGraph specialists can gather household context and search
documents, but cannot create or change anything. M7 deferred write capability
on purpose, citing the PRD directly: *"Phase 6... adoption of Model Context
Protocol (MCP) enabling safe agent execution over local and web-based
operations APIs."* M7 also specifically set up its Redis-backed checkpointer
"for Phase 6's likely next use of checkpointing (human-in-the-loop interrupts
before a write-capable tool runs)" — this milestone is that next use.

This plan gives ARIA's chat agent the ability to *act* — log a completed
maintenance item, create a reminder/schedule — gated by an explicit
confirm/cancel step so nothing is written to a household's data without the
user seeing and approving exactly what will happen first. It also actually
adopts the MCP protocol (not just Python functions dressed up as "tools"),
since MCP tooling is a named technical objective in the PRD's Executive
Summary, not just a phase title.

**Scope, narrowed up front (mirrors how M7 narrowed its own scope):**
1. **Two write tools only: `create_log` and `create_schedule`.** These cover
   the two highest-value conversational actions ("log that I changed the
   oil today," "remind me to rotate the tires every 6 months"). No
   `update_entity`, no `update_schedule`, no delete/archive — those stay
   deferred, same treatment M2 gave its multi-entity picker gap. Both wrap
   `core-api`'s **existing** `POST /logs` / `POST /schedules` REST endpoints
   exactly as they are today — **no core-api changes at all**. Permission
   checks (`check_permission` via `require_entity_for_create`), schedule
   validation (`ScheduleCreate`'s interval-type validator), and the
   due-date recompute logic all keep living exactly where they already do;
   the MCP layer is a thin protocol-compliant front door onto an unchanged
   backend, the same relationship `core_api_client.py` already has to
   `core-api` for reads.
2. **"Web-based operations APIs"** (the other half of the PRD's Phase 6
   line — e.g. a weather API, a parts-lookup API) has no concrete
   requirement behind it yet. Deferred, not built, same as M7 deferred
   cross-specialist handoff — revisit if a real use case shows up.
3. **The confirm/cancel safety gate applies to ARIA's own conversational
   write path only**, via a genuine LangGraph `interrupt()`. A third-party
   MCP client (e.g. Claude Desktop) connecting directly to the new MCP
   server and calling `create_log` would execute immediately — no worse
   than calling `core-api`'s REST endpoint directly today, since the MCP
   tool requires the same valid household session cookie either way. Real
   confirmation-at-the-tool-layer for arbitrary external MCP clients is a
   further hardening step with no concrete need yet — out of scope, called
   out explicitly rather than silently assumed away.
4. **No new cross-turn conversation memory.** The one exception:
   confirming or cancelling a proposed action reuses the *same* `thread_id`
   across exactly two HTTP requests (propose, then resume) — a narrow,
   single-purpose exception to M7's "fresh `thread_id` every request" rule,
   not a reversal of it. Everything else about the no-`conversation_id`
   contract is unchanged.

---

## Key design decisions

**A real MCP server, run as its own process — not mounted into
`ai-service`'s existing FastAPI app.** The official `mcp` Python SDK's
`FastMCP` is the de-facto way to ship this. Mounting a `FastMCP`
streamable-HTTP app onto an *existing* FastAPI app is a documented rough
edge (nested ASGI lifespans aren't recognized — see
`modelcontextprotocol/python-sdk#1367`), and `ai-service`'s main app has no
need to share a process with it anyway — this mirrors M7's own precedent
exactly: when a new capability has a different lifecycle/transport than
what's already running (there: LangGraph's checkpointer needing
RediSearch/RedisJSON that plain Redis doesn't have; here: MCP's own
streamable-HTTP server needing its own ASGI lifespan), it gets its own
small dedicated process instead of being forced into the existing one.
Concretely: `app/mcp_server.py` builds a `FastMCP` instance and calls
`mcp.run(transport="streamable-http", host="0.0.0.0", port=...)` directly;
a new docker-compose service (`mcp-server`) runs `python -m app.mcp_server`
from the *same* `ai-service` image/build (zero new Dockerfile, just a
different container command), so `mcp_tools.py` is shared code, not a
duplicated implementation.

**Tools take the session cookie as an explicit string argument, not a
forwarded HTTP header/cookie.** The only consumer that matters today is
ARIA's own graph, calling these functions in-process (see below) — there's
no need to solve "how does an arbitrary MCP client's transport carry a
cookie" when a plain `session_cookie: str` tool parameter says the same
thing more simply and works identically for any MCP client, present or
future. Consistent with this codebase's habit of picking the simplest
thing that satisfies the requirement (M6's hand-rolled `StreamFilter`
instead of a parser library; M7's flat `for` loop instead of graph-cyclic
edges).

**The LangGraph graph calls the same tool functions in-process — it does
not round-trip through the MCP protocol to talk to its own tools.** Exactly
mirrors M7's own precedent: `gather_household_context`/
`search_household_documents` wrap `entity_grounding`/`retrieval` directly,
called as plain Python, not over a protocol, even though the *concept* is
"a tool." `app/mcp_tools.py` holds the actual logic
(`create_log(cookie, args) -> dict`, `create_schedule(cookie, args) ->
dict`, both thin `httpx` POSTs to `core-api`, extending
`core_api_client.py`'s existing GET-only wrapper pattern with two new POST
wrappers); `app/mcp_server.py` registers `@mcp.tool()`-decorated wrappers
around those same two functions for real external MCP clients, and
`app/agents/nodes.py`'s new `execute_action_node` calls
`mcp_tools.create_log`/`mcp_tools.create_schedule` directly. One
implementation, two callers — same "can't silently drift apart" reasoning
`gather_baseline_context` was built for in M7.

**Three new graph nodes, not one — specifically to avoid LangGraph's
"a resumed node restarts from its own top" behavior wasting an LLM call.**
Verified via research before designing around it: when `interrupt()`
pauses a node and is later resumed, LangGraph re-runs that *entire node
function from the beginning* — it does not resume mid-function. A single
node that does an LLM tool-decision call and *then* calls `interrupt()`
would silently redo that LLM call every time the user confirms/cancels.
Splitting the write path into three single-purpose nodes sidesteps this
entirely:
- `propose_action_node`: does the one LLM decision call (which tool, which
  entity, what field values), sets `state["proposed_action"]` (`None` if it
  can't confidently parse one). Runs once, never re-entered.
- `confirm_gate_node`: if `proposed_action` is set, calls
  `interrupt(proposed_action)` — its *only* job, so re-running it on resume
  costs nothing. Sets `state["decision"]` from the resume value. If
  `proposed_action` is `None` (couldn't parse a confident action), skips
  the interrupt entirely and falls through.
- `execute_action_node`: reads `decision` — `"confirm"` calls the matching
  `mcp_tools` function and records the result (or the error, e.g. a 400
  from `core-api`'s own `ScheduleCreate` validation, surfaced back to the
  user rather than swallowed); `"reject"` records a cancellation note; a
  `proposed_action` of `None` records "couldn't determine a specific
  action" so the model can ask a clarifying question instead of guessing.

**One new supervisor category, `"action"` — the four existing
specialists are completely untouched.** Adding a fifth word to the same
one-word classification prompt (`_SUPERVISOR_SYSTEM_PROMPT`) that already
routes `maintenance`/`vehicle`/`research`/`general` is the smallest
possible change with zero risk to the read-path nodes M7 already shipped
and verified. `"action"` is described as: *"the user is asking you to
record, log, or schedule something — not just asking a question."* A
message that mixes a question and a request to log something routes to
whichever the model leads with; genuinely mixed-intent turns are an
accepted limitation, not solved here (same spirit as M7's "no
cross-specialist handoff" scope note).

**`routers/chat.py` grows a `resume` branch, not a new endpoint** — same
pattern M3–M7 all followed (extend `/chat`'s behavior/SSE vocabulary in
place). `ChatRequest` gains an optional `resume: {thread_id, decision}`.
When present, `chat()` skips classification/gathering entirely and resumes
the graph at that `thread_id` via `Command(resume=decision)` with a
freshly supplied cookie in `config` (the cookie is **never** written into
checkpointed state/Redis — only ever passed fresh per-request via
`config["configurable"]["cookie"]`, exactly as M7 already does for every
other node). When the graph run (fresh or resumed) ends by hitting
`confirm_gate_node`'s interrupt rather than reaching `END`, `chat()` emits
one new terminal SSE event, `action_proposed`
(`{thread_id, tool, label, summary}`), and closes the stream — **no Ollama
call happens this turn**, so ARIA can never stream a "done!" before the
action is actually confirmed. Detecting "the graph is paused at an
interrupt" from `astream(..., stream_mode="updates")`'s exact yielded
shape is a genuinely open question against the installed `langgraph`
version (same category of unknown M7 flagged for `AsyncRedisSaver.setup()`
and resolved by checking the installed version directly) — flagged as an
explicit verification step below rather than assumed.

**`build_system_prompt()` gains one more optional trailing note** for when
`action_result` is present in state (a completed action, a cancelled one,
or "couldn't determine one") — same shape as the existing
`NO_CONTEXT_SUFFIX`/`NO_DOCUMENTS_NOTE`/`NO_ENTITY_NOTE` fallback strings,
telling the model what just happened so it can acknowledge it naturally
("Done — logged the oil change for the Ranger.") instead of guessing.

---

## File-by-file plan

### `services/ai-service/pyproject.toml`
- Add `"mcp>=1.0"` to `dependencies`. Regenerate the workspace `uv.lock`.

### `services/ai-service/app/mcp_tools.py` (new)
- `async def create_log(session_cookie: str, args: dict) -> dict` — POSTs
  `args` to `core-api`'s `POST /logs` via `httpx`, forwarding
  `session_cookie` exactly like every `_get()` call in `core_api_client.py`
  does today, raising on a non-2xx so the caller can surface the real
  validation error.
- `async def create_schedule(session_cookie: str, args: dict) -> dict` —
  same shape, `POST /schedules`.
- Both live here (not `core_api_client.py`) since they're POSTs with a
  fundamentally different call shape (arbitrary write payload vs. today's
  fixed-path GETs) and are conceptually "the MCP tool surface," even though
  they share `core_api_client.get_client()`'s underlying `httpx.AsyncClient`
  singleton.

### `services/ai-service/app/mcp_server.py` (new)
- Builds a module-level `FastMCP("aria-household-ops")` instance.
- `@mcp.tool()`-decorated `create_log(session_cookie: str, entity_id: str,
  type: str, occurred_at: str, title: str, description: str | None = None,
  cost: float | None = None, metrics: dict[str, str] | None = None) ->
  dict` and a matching `create_schedule(...)` — thin argument-shape wrappers
  calling `mcp_tools.create_log`/`create_schedule`. Tool docstrings double
  as the MCP tool descriptions any MCP client (this graph, or an external
  one) sees.
- `if __name__ == "__main__": mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_server_port)`.

### `services/ai-service/app/config.py`
- Add `mcp_server_port: int = 8002`.

### `docker-compose.yml`
- New `mcp-server` service: same `build` as `ai-service` (the existing
  Python service Dockerfile, `SERVICE=ai-service` build arg), `command:
  python -m app.mcp_server` overriding the image's default uvicorn CMD,
  same environment block and bind mounts as `ai-service` (it needs
  `AI_SERVICE_CORE_API_URL` to reach `core-api`), new port mapping for the
  MCP endpoint. No new dependency on `agent-store`/`redis` — this process
  never touches LangGraph or the checkpointer, only `core-api`.

### `services/ai-service/app/agents/state.py`
- `VALID_AGENTS` grows to `(..., "action")`; `AGENT_LABELS["action"] =
  "ARIA"` (an action turn doesn't need its own specialist persona — the
  interesting part is the proposed action, not a distinct voice).
- `AgentState` gains `NotRequired` fields: `proposed_action: dict | None`,
  `decision: str | None`, `action_result: dict | None`.
- New `ACTION_PERSONA` constant (extends `BASE_SYSTEM_PROMPT` with
  "you can log completed maintenance/conversations and create reminders;
  never claim an action happened unless `action_result` says it did").

### `services/ai-service/app/agents/nodes.py`
- `_ACTION_DECISION_SYSTEM_PROMPT`: given the user's message, today's date,
  and a rendered list of the household's matched entities (id + name +
  domain — reuse `entity_grounding`'s existing word-boundary matching,
  factored out of its current `_find_matching_entities` into a
  module-level, non-underscored `find_matching_entities()` so this file can
  call it too instead of duplicating the regex), respond with strict JSON:
  `{"tool": "create_log"|"create_schedule"|null, "args": {...}, "summary":
  "<one-line plain-English description for the confirmation card>"}`.
  Reuses the same `get_adapter().parse_tool_decision()`-style parse (a
  generalization of the existing method to any JSON-shaped decision, not
  just the research tool's `{"tool", "query"}` shape — check whether
  `parse_tool_decision`'s existing markdown-fence-stripping logic already
  covers this shape as-is, since it just does `json.loads` after stripping
  fences; if so, no adapter change needed at all, just a wider consumer).
- `propose_action_node(state, config)`: builds the prompt above, calls
  `ollama.complete()`, parses. `tool` missing/null/entity unresolvable →
  `{"proposed_action": None}`. Otherwise `{"proposed_action": {"tool":
  ..., "args": ..., "summary": ...}}`. Never raises — any exception here
  degrades to `proposed_action = None`, same contract as every other node.
- `confirm_gate_node(state, config)`: if `state.get("proposed_action")` is
  `None`, return `{"decision": None}` immediately (no interrupt). Otherwise
  `decision = interrupt(state["proposed_action"])`; return `{"decision":
  decision}`.
- `execute_action_node(state, config)`:
  - `proposed_action` is `None` → `{"action_result": {"status":
    "unclear"}}`.
  - `decision == "reject"` → `{"action_result": {"status": "cancelled",
    "summary": state["proposed_action"]["summary"]}}`.
  - `decision == "confirm"` → call `mcp_tools.create_log`/`create_schedule`
    (dispatch on `proposed_action["tool"]`) with the cookie from
    `config["configurable"]["cookie"]`; success →
    `{"action_result": {"status": "done", "summary": ...}}`; an
    `httpx.HTTPStatusError` (e.g. `core-api`'s 400 on invalid schedule
    fields) → `{"action_result": {"status": "failed", "detail":
    <response body's error>}}` — surfaced to the user, not swallowed,
    since this is an explicit user-requested action, not passive grounding.
- Also gathers baseline context (`gather_baseline_context`, unchanged) in
  the `proposed_action is None` case only, concurrently with the decision
  call, so a "couldn't tell what you meant" answer still has grounding to
  work with when the model asks a clarifying question — same
  never-answer-from-nothing standard M7's code review already established
  for every other specialist.

### `services/ai-service/app/agents/graph.py`
- `build_graph_builder()`: `add_node` for the three new functions.
  `_route` gains `"action"` in its target map, pointing at
  `propose_action_node`. Edges: `propose_action_node` →
  `confirm_gate_node` → `execute_action_node` → `END`. (`execute_action_node`
  branches internally on `decision`/`proposed_action`, per above — no
  conditional *edges* needed, keeping this as flat as M7's existing
  four-specialist shape.)
- No change to `_build_graph()`'s checkpointer setup — `interrupt()`
  requires a checkpointer, which M7 already wired up; this is exactly the
  use case it was built for.

### `services/ai-service/app/routers/chat.py`
- `schemas/chat.py`: new `ChatResume(BaseModel)` — `thread_id: str`,
  `decision: Literal["confirm", "reject"]`. `ChatRequest` gains
  `resume: ChatResume | None = None`.
- `_route_and_gather` (or a new sibling function alongside it — naming
  TBD during implementation, likely `_resume_action`) handles the
  `request.resume is not None` branch: builds `config =
  {"configurable": {"cookie": session_cookie, "thread_id":
  request.resume.thread_id}}`, calls
  `graph.astream(Command(resume=request.resume.decision), config,
  stream_mode="updates")`, drains to a final state exactly like the
  existing loop.
- New helper to detect an interrupted (not-yet-`END`) run from the
  `astream` output — exact shape verified empirically against the
  installed `langgraph` version (sequencing step below) before finalizing
  this function's body.
- `chat()`: routes to the resume path when `request.resume` is set,
  otherwise today's classify/gather path (now including the `"action"`
  branch via the same `_route_and_gather`/graph call — no separate
  "is this an action request" pre-check needed, the supervisor handles
  that same as every other category).
- If the graph run ends interrupted: `_event_stream()` yields
  `_sse("action_proposed", {"thread_id": ..., "tool": ..., "label": ...,
  "summary": ...})` and returns — no `citations`/`thinking`/`token`/Ollama
  call this turn.
- If the graph run reaches `END` (classify path or resume path both):
  unchanged flow, except `build_system_prompt()` also receives
  `action_result` (new optional parameter) when present, appending the new
  trailing note described above.

### `services/ai-service/app/adapters/base.py`, `qwen.py`
- Likely **no changes** — `parse_tool_decision()`'s existing
  fence-stripping + `json.loads` already handles an arbitrary JSON-object
  shape; `propose_action_node` just expects a wider set of keys
  (`tool`/`args`/`summary` instead of `tool`/`query`). Confirm this during
  implementation before touching the adapter at all.

### `services/ai-service/tests/`
- `test_mcp_tools.py` (new): fake `httpx` responses for `create_log`/
  `create_schedule`, following the existing `FakeAsyncClient`/monkeypatch
  convention — success and a validation-error (400) case each.
- `test_agents.py`: new cases for `propose_action_node` (confident
  decision; no matching entity; malformed JSON → all degrade to `None`
  cleanly), `confirm_gate_node` (skips interrupt when `proposed_action` is
  `None`; calls `interrupt()` otherwise — using LangGraph's own interrupt
  testing pattern, likely via the `MemorySaver`-backed integration test
  already established in this file, resuming with `Command(resume=...)`),
  `execute_action_node` (confirm/reject/unclear paths; a mocked
  `mcp_tools.create_schedule` raising `HTTPStatusError` surfaces as
  `status: "failed"`, never raises out of the node).
- `test_chat.py`: new cases — an `"action"`-classified request with a
  confident decision emits `action_proposed` and stops (no `token` events
  at all this turn); a follow-up request carrying `resume` against that
  same monkeypatched graph produces `action_result`-flavored
  `citations`/`thinking`/`token` output afterward, for both `"confirm"`
  and `"reject"`.

### `services/frontend/src/api/chat.ts`
- `ChatMessage`/existing types unchanged. New exported type
  `ProposedAction = { threadId: string; tool: string; label: string;
  summary: string }`.
- `StreamChatHandlers` gains `onActionProposed?: (action: ProposedAction) => void`;
  `dispatchFrame` gains an `action_proposed` case.
- `streamChatMessage(messages, handlers, signal, resume?)` gains an
  optional fourth parameter forwarded as `{ messages, resume }` in the
  POST body (matching the new `ChatResume` shape — `snake_case` on the
  wire, same convention as every other field here).

### `services/frontend/src/hooks/useStreamChatMessage.ts`
- `send(messages, callbacks)` unchanged in shape; threads `onActionProposed`
  through. New exported method `resumeAction(threadId, decision, callbacks)`
  calling `api.streamChatMessage([], callbacks, signal, { threadId, decision })` —
  the resume call doesn't need real message history since the graph state
  is already checkpointed under `threadId`.

### `services/frontend/src/pages/ChatPage.tsx`
- New state: `pendingAction: ProposedAction | null`.
- `onActionProposed`: sets `pendingAction`, does **not** create the
  assistant streaming placeholder this turn (mirrors how `onAgent` today
  only stashes a ref rather than rendering anything by itself).
- New confirmation card rendered between the message list and the input
  form when `pendingAction` is set: the plain-English `summary`, a
  **Confirm** and a **Cancel** button. Clicking either calls
  `sendMessage.resumeAction(pendingAction.threadId, "confirm" | "reject",
  {...same onCitations/onThinking/onToken handlers `send()` already
  builds...})`, clears `pendingAction`, and lets the resulting answer
  stream into a new assistant bubble exactly like any other turn.
- Input form disabled while `pendingAction` is set (mirrors the existing
  `sendMessage.isPending` disable) — a household member shouldn't be able
  to fire a second message while a write is awaiting confirmation.

### `services/frontend/src/components/ChatBubble.tsx`
- No changes expected — the confirmation card is page-level UI, not a
  message bubble variant.

---

## Sequencing

1. **Verify the `mcp` SDK's standalone `FastMCP.run(transport=
   "streamable-http")` shape against the installed version** — confirm it
   binds host/port as expected and a plain `mcp` client (or `curl` against
   its session/initialize handshake) can list and call the two tools with
   a `session_cookie` argument. Do this *before* wiring it into
   docker-compose, same discipline as M7 checking `AsyncRedisSaver` against
   the real installed package before writing `graph.py` around an assumed
   API.
2. `app/mcp_tools.py`, `app/mcp_server.py`, `pyproject.toml` +
   `docker-compose.yml`'s new `mcp-server` service. Verify live: `docker
   compose up mcp-server`, confirm `create_log`/`create_schedule` succeed
   against the real running `core-api` with a real session cookie, and
   that a validation error (e.g. a schedule missing a required
   interval-specific field) surfaces `core-api`'s actual 400 detail rather
   than a generic failure.
3. `entity_grounding.find_matching_entities()` (renamed/exported),
   `agents/state.py`'s new fields, `agents/nodes.py`'s three new node
   functions + their unit tests (using `MemorySaver`, not real Redis, same
   as M7's existing graph-level test) — **verify LangGraph's
   `interrupt()`/`Command(resume=...)` behavior directly against the
   installed `langgraph` version here**: confirm a node re-runs from its
   top on resume (the assumption the three-node split is built on), and
   pin down exactly what `astream(..., stream_mode="updates")` yields when
   a run is currently paused at an interrupt vs. when it reaches `END` —
   this is the one genuinely open technical question in this plan,
   parallel to M7's own upfront check of `qwen3:14b`'s classification
   reliability before building the supervisor around it.
4. `agents/graph.py` wiring (`"action"` route + the three-node chain).
5. `routers/chat.py` (`ChatResume` schema, the resume branch, interrupt
   detection, the `action_proposed` SSE event, `build_system_prompt()`'s
   new trailing note) + `test_chat.py`. Verify against the real running
   stack with `curl -N` for: an action request that proposes, then a
   second `curl` carrying `resume` that confirms; and again for reject.
6. Frontend: `api/chat.ts`, `useStreamChatMessage.ts`, `ChatPage.tsx`.
   Confirm `npm run lint`/`npm run build` clean.
7. Manual browser walkthrough end-to-end (below).

---

## Verification

**Manual walkthrough (happy path):** log in, open Chat.
- "Log that I changed the oil on the Ranger today" — confirm a
  confirmation card appears with a plain-English summary naming the
  correct vehicle entity and today's date, **no assistant answer streams
  yet**. Click Confirm — confirm a new log entry actually exists (check
  the Ranger's entity detail page / logs list) and ARIA's follow-up answer
  acknowledges it naturally.
- "Remind me to rotate the tires every 6 months" — same shape, confirm the
  created schedule shows up in "what's due" once its date arrives, or at
  least exists via the entity's schedules list immediately after.
- Propose an action, click **Cancel** — confirm nothing was written
  (no new log/schedule), and ARIA's follow-up acknowledges the
  cancellation rather than claiming it happened.
- Ask a request with no resolvable entity ("log that I fixed the thing") —
  confirm no confirmation card appears; ARIA asks a clarifying question
  instead (grounded in whatever baseline context it gathered).
- Ask an ordinary question in between (unrelated to any pending action) —
  confirm normal M3–M7 chat is completely unaffected.

**Degrade-path walkthrough:**
- Stop `mcp-server` mid-session, propose and confirm an action — confirm
  `execute_action_node`'s `httpx` call fails cleanly, `action_result`
  reports `"failed"`, and ARIA's answer says the action didn't go through
  rather than the request 500ing or hanging.
- Stop `agent-store` (Redis) before proposing an action — confirm this
  degrades exactly like M7's existing Redis-down path (blanket M4/M5
  grounding, no `agent`/`action_proposed` frame at all) rather than
  crashing on the new interrupt-dependent code path.
- Stop `core-api` after a confirm — same `"failed"` surfacing as the
  `mcp-server`-down case above.

**Automated:** `uv run pytest` in `ai-service` (new `test_mcp_tools.py`,
extended `test_agents.py`/`test_chat.py`); `npm run lint` + `npm run build`
in `frontend`. As with every prior AI milestone, the confirmation card's
actual rendering/click-through is verified by the manual walkthrough only —
no frontend unit test runner exists in this project.

### Critical files
- `services/ai-service/app/mcp_tools.py`
- `services/ai-service/app/mcp_server.py`
- `services/ai-service/app/agents/nodes.py`
- `services/ai-service/app/agents/graph.py`
- `services/ai-service/app/agents/state.py`
- `services/ai-service/app/routers/chat.py`
- `services/ai-service/app/schemas/chat.py`
- `services/ai-service/app/entity_grounding.py`
- `docker-compose.yml`
- `services/frontend/src/api/chat.ts`
- `services/frontend/src/hooks/useStreamChatMessage.ts`
- `services/frontend/src/pages/ChatPage.tsx`
