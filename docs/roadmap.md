# ARIA — MVP Roadmap

**Status:** living document. Read before starting any new work; update it
when a milestone completes or the plan changes. This is intentionally
high-level — each milestone gets its own sub-task plan (via `EnterPlanMode`
or a scratch planning doc) when it's actually picked up. Don't plan milestone
N+2 in detail while N is still open.

Source of truth for *why* these milestones exist: `local/homeops_ai_prd.pdf`.
Source of truth for *current shape* of the code: [`architecture.md`](architecture.md)
and [`data-model.md`](data-model.md). This doc is the bridge between them —
the sequenced path from today's scaffolding to a usable MVP.

Status legend: ✅ done · 🚧 in progress · ⬜ not started

---

## Guiding principles (from the PRD — keep enforcing these as we build)

- **Solve real problems first.** Plain CRUD/relational lookups (find a part
  number, list what's due) must stay fast and functional without invoking an
  LLM. AI is additive, not load-bearing for basic tracking.
- **Piecewise delivery.** Every milestone below ships something a household
  member could actually use — not just an API contract or a schema.
- **Strict decoupling.** `core-api` (Mongo CRUD) must keep working if
  `ai-service`/`chromadb`/`ollama` are down. The milestone order below is
  chosen so CRUD tracking (M1) lands and is independently useful *before*
  any AI capability (M3+) is built on top of it.

---

## Where we are today (M0 — done)

Scaffolding across all 4 services, proven end-to-end via `docker compose up`:
- Shared Pydantic schema (`aria-shared`) for entities/logs/schedules/documents,
  covering all 4 domains (home, vehicle, equipment, project).
- `core-api`: Mongo connectivity, health check, one read endpoint
  (`GET /entities`).
- `ai-service`: Chroma + Ollama clients wired up, health check only — nothing
  calls them yet.
- `worker`: Celery skeleton, one `ping` task — no real pipeline.
- `frontend`: health-check dashboard only — no feature UI.
- No auth yet; `household_id` is passed as a query param, not derived from a
  session.

Full detail: [`architecture.md`](architecture.md) → "Where things stand".

---

## Milestones to MVP

### M1 — Core CRUD tracking ✅
**The foundation milestone. Nothing AI-related should start before this is usable.**

- `core-api`: write endpoints (`POST`/`PATCH`/`DELETE`, or archive-in-place)
  for `entities`, `logs`, and `schedules`. `GET` endpoints beyond the
  existing `entities` list (single entity, logs-for-entity, schedules
  due-soon).
- Schedule due-date recompute logic (`data-model.md` §5) — triggered when a
  linked log is created.
- Minimal auth: real session-derived `household_id` instead of a query
  param, even if login is a single hardcoded household to start.
- `frontend`: real feature UI — list/create/edit/archive for entities across
  all 4 domains, log entry, and a "what's due" view. This is the first
  milestone where a household member could use ARIA for its stated purpose
  with zero AI involved.

**Exit criteria:** someone can add a vehicle, log an oil change against it,
set a recurring schedule, and see it show up as "due" later — entirely
through the UI, no AI service involved.

**Done as of 2026-07-16.** Landed with more than the original scope called
for: real `DELETE` (not just archive) on entities/logs/schedules, `PATCH`
on logs, a working `aria_auth` permissions system (not just the unused seam
noted below), a `Person` entity domain alongside the original 4, a
profile page, and theming. See `git log` (`f74d89b`..`a75911b`) and the
`scaling-debt.md` series for the incremental hardening that happened
alongside feature work.

### M2 — Document ingestion hub ✅
Implements the PRD §3 pipeline and gives `worker` its first real job.

- `core-api`: file upload endpoint, `Document` records, raw bytes stored in
  S3-compatible object storage — MinIO locally, real S3 in production, both
  via the same `boto3` client code (endpoint URL is the only thing that
  changes between environments).
- `worker`: OCR task (Tesseract/Pillow) → context extraction → deterministic
  chunking (honors section headers, keeps page numbers) → embed → write to
  Chroma. Each stage updates `Document.processing_status`.
- `frontend`: upload UI, document list per entity, processing-status
  indicator.

**Exit criteria:** upload a manual/receipt PDF, watch it move through
`pending → ocr_complete → chunked → embedded`, and see it listed against the
entity it's attached to. Still no chat — this milestone is purely the
ingestion side.

**Done as of 2026-07-16.** Landed per `docs/plans/m2-document-ingestion-hub.md`
— verified end-to-end via a real PDF upload through the running
docker-compose stack (OCR read actual page text, chunking correctly
detected a section header, embedding + Chroma write matched the
`{document_id}:{chunk_index}` id shape in `data-model.md` §8) and via the
UI (upload → live status-badge polling through to `embedded` → download →
delete), plus the strict-decoupling check (worker/redis/chromadb/ollama
stopped, MinIO up: upload/list/download/delete on `core-api` all still
worked, document stayed `pending`).

**Known follow-up (not yet built):** `Document.entity_ids` is many-to-many
in the schema and API (`POST /documents` already accepts multiple
`entity_ids`, and entity-delete cascade cleanup — added 2026-07-16 —
already relies on that shape: deleting one referencing entity just unlinks
it, only deleting the document's Mongo/S3/Chroma state once nothing
references it anymore), but the upload UI has no multi-entity picker —
`DocumentUploadForm`/`useUploadDocument`/`uploadDocument()` are all
hardcoded to the single entity whose detail page you're uploading from.
Pick this up whenever cross-entity document linking (e.g. a receipt
covering two items) needs to be user-facing, not just API-capable.

### M3 — AI Phase 1: Basic AI chat ✅
PRD Phase 1. Direct, stateless conversation with the local `ollama` model —
no retrieval, no persistence.

- `ai-service`: `POST /chat` — forwards to Ollama, returns a completion.
- `frontend`: minimal chat UI.

**Exit criteria:** ask ARIA a general question, get a model response. No
grounding yet — this milestone just proves the request path end-to-end.

**Done as of 2026-07-16.** Landed per `docs/plans/m3-basic-ai-chat.md`.
`ai-service` gained a `ModelAdapter` seam (`app/adapters/`) not called for
in the original scope — `qwen3:14b` prefixes replies with a `<think>...`
reasoning block, and the `QwenAdapter` strips it before the response
reaches the frontend, selected via `AI_SERVICE_MODEL_ADAPTER` so a future
model swap doesn't require touching the router. Chat message schemas
(`app/schemas/chat.py`) deliberately live in `ai-service`, not
`aria_shared` — everything in the shared lib is a Mongo-persisted
contract, and chat messages are request-scoped only (no persistence,
per PRD Phase 1). `POST /chat` is unauthenticated at the service level
(gating is frontend-only, via `RequireAuth`), matching strict decoupling:
`ai-service` has no session/db dependency at all.

Verified end-to-end 2026-07-16: real request against the running
`qwen3:14b` model returned a clean, think-block-stripped completion;
system-role injection and empty-message payloads both correctly 422;
all 9 `ai-service` pytest cases passed after rebuilding the container
(the running image predated this commit); frontend `lint`/`build`
(`tsc -b && vite build`) both clean. Not verified: an actual browser
click-through of `ChatPage.tsx` — no browser tooling available in this
session, so the frontend check leaned on code review + a clean build
rather than a driven UI walkthrough.

### M4 — AI Phase 2: Naive RAG ✅
PRD Phase 2. Chat gets grounded in whatever M2 has embedded.

- `ai-service`: similarity search against Chroma for the incoming question,
  inject top-k chunks into the prompt window before calling Ollama.

**Exit criteria:** ask about something covered in an uploaded manual, get an
answer that reflects the document's actual content (even without a visible
citation yet).

**Done as of 2026-07-16.** `ai-service` gained `app/retrieval.py`
(`retrieve_context(query)`: embeds the latest user message via the
existing `ollama.embed()`, queries Chroma's `documents` collection —
`app/chroma.py` grew a `get_documents_collection()` mirroring `worker`'s —
for the top `AI_SERVICE_RAG_TOP_K` chunks, default 4) and
`routers/chat.py::build_system_prompt()`, which extends the M3 system
prompt with the retrieved excerpts when any exist, or an "no relevant
documents" caveat when none do. Retrieval degrades to ungrounded (M3-style)
chat on any failure — Ollama or Chroma unreachable, or an empty collection
— per strict decoupling; this was verified live by stopping `chromadb`
mid-session and confirming `/chat` kept returning `200` with a `logger.warning`
in `ai-service`'s logs, then confirming grounded answers resumed after
restarting `chromadb` with no `ai-service` restart needed. Also verified
live against the real chunks left over from M2 testing: asking "what does
the uploaded document say, word for word?" returned the exact chunk text
back verbatim. No frontend changes were needed — `ChatPage.tsx` only ever
rendered `message.content`, which now happens to be grounded.

**Known follow-up (accepted debt, not built):** retrieval is **not**
scoped by household — Chroma's chunk metadata (`data-model.md` §8) has no
`household_id` field, so a similarity query searches every household's
embedded documents. This is a real gap for a genuinely multi-household
deployment, but harmless today since `core-api/app/seed.py` seeds exactly
one household and multi-household is explicitly deferred post-MVP (see
below). Revisit alongside that work — it'll need a `worker` change to
write `household_id` into new chunk metadata, a backfill for
already-embedded chunks, and giving `ai-service` a session/household
concept it has never had.

### Household data grounding (fast-follow to M3/M4) ✅
Not a numbered PRD milestone — a direct fix for a reported gap in the
shipped chat feature: chat only grounded in uploaded documents (M2/M4),
never in the household's own entities/logs/schedules, so a fact recorded
as a log note or entity tag (e.g. a Person entity tagged "Dad" with a log
mentioning a book he wrote) was invisible to chat regardless of tagging.

- `ai-service` gained `app/entity_grounding.py` (word-boundary,
  case-insensitive match of the latest user message against every
  household entity's `name`/`tags`, capped at `AI_SERVICE_ENTITY_MATCH_LIMIT`
  matches) and `app/core_api_client.py` (thin async client forwarding the
  browser's `aria_session` cookie to core-api's existing
  `GET /entities` / `GET /entities/{id}/logs` / `GET /entities/{id}/schedules`
  — no core-api code changes were needed). Matched entities' tags, specs,
  person-specific attributes, recent logs (capped at
  `AI_SERVICE_ENTITY_LOGS_LIMIT`, default 5), and schedules are rendered
  into a "Relevant household records" section of the system prompt
  alongside M4's document excerpts.
- `ai-service` gained a real `aria-auth` dependency (for the
  `SESSION_COOKIE_NAME` constant only — no Mongo client, no session
  validation locally; core-api still does that) and CORS changed from
  wildcard/no-credentials to an explicit `frontend_origin` +
  `allow_credentials=True`, mirroring `core-api`'s own setup, since a
  cookie can only be forwarded cross-origin with both sides configured
  for it.
- Degrades to ungrounded chat (M3/M4-style) on every failure axis — no
  cookie, an expired/invalid session (core-api 401), or core-api
  unreachable — logged at three different severities (nothing for "no
  cookie," `info` for an expired session, `warning` for a real outage) so
  routine cookie-less traffic doesn't drown out genuine failures.

**Verified end-to-end 2026-07-16** against real household data (a Person
entity "Allen Woodward" tagged `Dad`/`Father`/`Allen`, a log "Dad finished
his book!...", and a "Read Dad's Book" schedule due 2026-07-17): asking
"give me some talking points for my next call with Dad" returned a
response that referenced the book and the schedule's exact due date.
Confirmed the same question with no session cookie, and again with
`core-api` stopped entirely, both returned a generic, ungrounded `200`
response with no error surfaced — and confirmed grounding resumed after
restarting `core-api` with no `ai-service` restart needed. All 33
`ai-service` pytest cases pass; `frontend` `lint`/`build` unaffected
(the only frontend change is `credentials: 'include'` in
`api/chat.ts`).

### M5 — AI Phase 3: Document citations ✅
PRD Phase 3. Traceable referencing.

- `ai-service`: prompt/response shape carries page, paragraph, and source
  filename for each retrieved claim (resolved via Chroma chunk metadata →
  `documents` record, per `data-model.md` §8).
- `frontend`: render citations as clickable references back to the source
  document.

**Exit criteria:** an answer visibly cites "Water Heater Manual, p.4" and
clicking it shows the source.

**Done as of 2026-07-17.** Landed per `docs/plans/m5-document-citations.md`
(commits `62384ae`, `ba8ef7d` — this status marker was just never flipped
until now). `ai-service` gained `app/citations.py::resolve_citations()`
(dedups retrieved chunks by `(document_id, page_number)`, resolves each via
`core_api_client.get_document()`, degrades to `[]` on any failure —
no-cookie, expired session, or `core-api` down — same contract as the
household-grounding fast-follow) and `ChatResponse.citations`. Beyond the
original scope: citations and household-entity grounding got
cross-referenced — `Citation.entity_ids` links a cited document back to any
matched entity it's attached to, so `build_system_prompt()` can tell the
model "this document is linked to Dad" instead of surfacing the two context
sources as unrelated. `frontend` renders resolved citations as a
"Sources" pill row under each assistant `ChatBubble`, linking to the
existing document `downloadUrl()`; `ChatMessage` (the wire type resent as
conversation history) deliberately stays `{role, content}` only, with
citations tracked in page-local state, since `ai-service`'s
`ChatRequest.messages` schema is `extra="forbid"` and would reject a
resent message carrying a `citations` field.

### M6 — AI Phase 4: Streaming responses ✅
PRD Phase 4. Polish pass on M3–M5, not new capability.

- `ai-service`: SSE endpoint for chat.
- `frontend`: render tokens as they arrive instead of waiting for the full
  response.

**This is the MVP finish line.**

**Done as of 2026-07-17.** Landed per
`docs/plans/m6-streaming-responses.md` (commit `ed5b6c9` — this status
marker was, again, just never flipped until now). `POST /chat` was
converted in place to a `text/event-stream` response with four named SSE
events (`citations` once and first, then `thinking`/`token` deltas, and an
`error` frame for a mid-stream Ollama failure) instead of forking a new
endpoint. `ModelAdapter` gained `create_stream_filter()`; `QwenAdapter`'s
implementation is a three-state machine (`detecting_open` / `thinking` /
`answering`) that classifies each streamed delta against `qwen3:14b`'s
`<think>...</think>` prefix even when a tag splits across chunks, so
reasoning text streams live to the frontend as a temporary, never-persisted
preview and is fully replaced by the real answer the moment it starts.
`ollama.py` gained `chat_stream()` (parses Ollama's NDJSON framing) without
touching the existing non-streaming `chat()`. `frontend` replaced
`useSendChatMessage` (react-query mutation) with a `useState`/`useRef`-based
`useStreamChatMessage` hook hand-rolling SSE parsing over
`fetch()`'s `ReadableStream` (`EventSource` can't do `POST` with a body).
Verified live against the real running stack per the plan's manual
walkthrough, including the mid-stream `error`-frame path (stopped `ollama`
after streaming had started) and the unchanged M4/M5 strict-decoupling
checks (`chromadb`/`core-api` stopped independently).

Beyond the original scope, this commit also shipped a RAG quality fix that
had nothing to do with streaming: `ai-service`/`worker` now embed via a
dedicated `AI_SERVICE_EMBED_MODEL`/`WORKER_EMBED_MODEL`
(`nomic-embed-text`) instead of the chat model (`qwen3:14b` made
empirically weak embeddings for paraphrased queries), and
`retrieval.py` drops any chunk past `AI_SERVICE_RAG_MAX_DISTANCE` (default
`0.9`, calibrated against this household's corpus — see
`docs/architecture.md`) instead of always returning `rag_top_k` chunks
regardless of relevance.

---

## Definition of MVP

When M1–M6 are done, a household member can:
- Track homes, vehicles, equipment, and projects with full CRUD, including
  freeform specs for long-tail fields (paint codes, sparkplug gaps, etc.).
- Log service/repair/inspection/expense/note/milestone history against any
  entity and see recurring maintenance schedules stay accurate automatically.
- Upload manuals/receipts/invoices and have them OCR'd, chunked, and
  embedded without any manual intervention.
- Ask a conversational AI questions grounded in those documents, with
  streamed responses and citations back to the exact source page.
- Do all of the above with the base tracking (M1) fully functional even if
  the AI stack is offline — per the "strict decoupling" principle.

## Post-MVP milestones

### M7 — AI Phase 5: Multi-agent orchestration ✅
PRD Phase 5. First step past MVP: specialized agents coordinating through a
stateful runtime, instead of M1–M6's single flat `/chat` prompt.

- `ai-service`: a LangGraph `StateGraph` (new `app/agents/`) with a
  supervisor node that classifies each request into one of four
  specialists — **Maintenance Agent**, **Vehicle Specialist**, **Research
  Assistant**, or a **General** fallback — each with its own persona and a
  curated subset of {household-entity grounding, document retrieval} as
  tools (both wrap the existing M4/`entity_grounding.py`+`retrieval.py`
  functions verbatim, not a new fine-grained tool surface). Research
  Assistant additionally gets a small bounded iterative loop over document
  search — the one place real agentic tool-choice adds value this
  milestone. Graph state is checkpointed to Redis, via a new dedicated
  `agent-store` service (Redis Stack — LangGraph's checkpointer needs the
  RediSearch/RedisJSON modules plain Redis doesn't have), kept separate
  from the `redis` service core-api/worker use for Celery so an
  agent-orchestration outage can't touch that queue — so orchestration is
  a genuinely stateful runtime per the PRD, though full cross-turn
  conversational memory is explicitly out of scope — the frontend still
  resends full history every request, unchanged since M1.
- `ai-service`: `POST /chat` converted in place again (same pattern as
  M3–M6) — gains one new SSE event, `agent`, naming which specialist
  handled the turn, emitted before `citations`/`thinking`/`token`. Falls
  back to today's (M5-era) blanket entity-grounding + retrieval behavior,
  with no agent framing, if the graph/Redis layer is unavailable —
  preserving strict decoupling for a new failure axis this milestone
  introduces.
- `frontend`: `ChatBubble` shows which specialist answered (e.g. "Vehicle
  Specialist").

**Exit criteria:** ask a vehicle-specific question and a document question
in the same session; see each answered by a differently-labeled
specialist, grounded appropriately, with the base M1 tracking UI and
M3–M6 chat still fully usable if this new orchestration layer degrades.

**Explicitly out of scope this milestone** (see
`docs/plans/m7-multi-agent-orchestration.md` for the full reasoning):
agent write-capability (create/update logs, schedules, entities) — PRD
Phase 6 (MCP) is explicitly billed as "safe agent execution," so mutating
actions land there with real guardrails instead of being bolted on here;
cross-specialist handoff/multi-hop coordination; fine-grained per-tool
argument schemas beyond the existing coarse grounding/retrieval calls;
frontend `conversation_id` / true cross-turn memory.

**Done as of 2026-07-18.** Landed per
`docs/plans/m7-multi-agent-orchestration.md`, largely as scoped, with two
real deviations discovered during implementation (both corrected in the
plan doc, not just here):
- **Redis-backed checkpointing needed its own instance, not the shared
  one.** Live testing against the plain `redis:7-alpine` Celery already
  uses failed outright (`unknown command 'FT._LIST'`) —
  `langgraph-checkpoint-redis` requires the RediSearch/RedisJSON modules,
  which stock Redis doesn't ship. Rather than upgrade the Celery-critical
  shared instance for an AI-layer feature, `ai-service` got its own
  `agent-store` service (`redis/redis-stack-server`) — one new container,
  scoped to this feature alone, zero blast radius on Celery. Verified
  live: `AsyncRedisSaver.setup()` succeeds, an `EntityContext`/
  `RetrievedChunk` dataclass round-trips through a real checkpoint (via
  `graph.ainvoke()` + `aget_state()`) with no manual serialization layer
  needed, and the historical `AsyncRedisSaver.setup()` "coroutine never
  awaited" upstream bug is already fixed in the installed version
  (`langgraph-checkpoint-redis==0.5.1`) — the sync-saver workaround the
  plan called for turned out to be unnecessary.
- **No native Ollama tool-calling anywhere** — confirmed via research
  before writing a line of code (a documented Qwen3/Ollama `tools`-param
  bug) and stuck to for the whole milestone: the supervisor's routing
  decision and the Research Assistant's tool-choice are both plain
  content completions (`ollama.py::complete()`, new) parsed by hand — one
  word for routing, a small JSON object for the tool decision — reusing
  `QwenAdapter.normalize_response()` to strip Qwen3's `<think>` block
  first. Verified live against the real running `qwen3:14b`: every test
  query classified sensibly and the JSON tool-decision parsed cleanly
  even though the model's raw reply was 1–2KB of reasoning before the
  actual one-word/JSON answer.

Verified end-to-end against the real running stack: a vehicle question
and a document question in the same session routed to "Vehicle
Specialist" and "Research Assistant" respectively (`agent` SSE frame
first, before `citations`); an authenticated request referencing seeded
household data ("talking points for my call with Dad") routed to
"general" and produced an answer genuinely grounded in that person's
actual logged details — confirming `gather_household_context` works
correctly from *inside* the graph, not just in isolation; stopping
`agent-store` mid-conversation dropped the `agent` frame entirely but
kept answers grounded via the direct-call fallback, and restarting
`agent-store` resumed agent routing with no `ai-service` restart needed
(its RediSearch index survived the stop/start cycle). All 93
`ai-service` pytest cases pass (extended `test_ollama.py`, new
`test_agents.py`, rewritten `test_chat.py` — including a regression case
proving `general` routing behaves identically to pre-M7 blanket
grounding); `core-api`'s 77 cases still pass unaffected by the shared
workspace `uv.lock` update; `frontend` `lint`/`build` both clean. Not
verified: an actual browser click-through of the new "X is looking into
this…" preview label and per-message specialist caption in
`ChatBubble.tsx` — no browser tooling available in this session, same
gap M3 and M6 noted; the frontend wiring was checked via code review plus
a clean typecheck/build rather than a driven UI walkthrough.

**Post-ship code review (same day) found and fixed 9 issues**, most
notably: `research_node` could crash uncaught on a null-content Ollama
response (an exception-handling gap — `_parse_tool_decision()` sat
outside the try/except that guarded the model call, unlike the already-
correct `supervisor_node`); Maintenance Agent/Vehicle Specialist never
searched documents and Research Assistant never gathered entity context,
a real capability regression from pre-M7 blanket grounding (see the
"Revised after code review" note in the plan doc for the fix — all four
specialists now share one baseline-gather helper); the classifier's
fixed-tuple-order substring scan could misroute a reply naming two
categories (now picks whichever label the model mentions first); a
markdown-fenced JSON reply silently disabled Research Assistant's search
tool (now stripped before parsing); every chat turn was writing a
permanent, never-expiring checkpoint into `agent-store` with no cleanup
path (now a 60-minute TTL); and a redundant `astream()` + `aget_state()`
Redis round trip was collapsed into one `ainvoke()` call. All fixes
verified live against the real stack (including a direct reproduction of
the null-content crash, and a live TTL check confirming checkpoint keys
now expire) and covered by new/updated tests — 101 `ai-service` pytest
cases pass.

### M8 — AI Phase 6: MCP integration ✅
PRD Phase 6. First write-capable agent action, gated behind an explicit
confirm/cancel step — everything M1–M7 could do was read-only.

- `ai-service`: two write tools only, `create_log`/`create_schedule`,
  wrapping `core-api`'s existing `POST /logs`/`POST /schedules` REST
  endpoints unchanged (no core-api business-logic changes — see the
  `libs/shared` schema move below). A real MCP server (`app/mcp_server.py`,
  the official `mcp` SDK's `FastMCP`, `streamable-http` transport) runs as
  its own process — a new `mcp-server` docker-compose service on the same
  `ai-service` image — since mounting it into the existing FastAPI app
  isn't supported cleanly. ARIA's own LangGraph agent calls the same
  `app/mcp_tools.py` functions in-process (no protocol round-trip to
  itself), mirroring M7's `gather_household_context` precedent.
- `ai-service`: new `"action"` supervisor category and a three-node write
  path (`propose_action_node` → `confirm_gate_node` → `execute_action_node`)
  specifically to avoid re-running an LLM call when LangGraph resumes a
  node from its top after an `interrupt()`. `POST /chat` gained a `resume`
  branch (`ChatResume{thread_id, decision}`) and a new terminal
  `action_proposed` SSE event — no Ollama call happens until the user
  confirms.
- `frontend`: a confirmation card (`ChatPage.tsx`) showing the proposed
  action's plain-English summary with Confirm/Cancel, input disabled while
  one is pending.

**Exit criteria:** ask ARIA to log a completed maintenance item or create a
reminder; see a confirmation card naming the exact action before anything
is written; confirming creates the real log/schedule, cancelling writes
nothing, and ordinary read-path chat (M3–M7) is unaffected.

**Done as of 2026-07-20.** Landed per
`docs/plans/m8-mcp-integration.md` (commit `c7b4211` — same "status marker
never flipped" gap as M5/M6). Notable deviations from the plan (corrected
in the plan doc's own post-implementation note, not just here):
`confirm_gate_node` was merged into `execute_action_node` (the graph is
just `propose_action_node` → `execute_action_node` → `END`, which calls
`interrupt()` itself); the supervisor's classifier word is
`"log_or_schedule"`, not `"action"`, to avoid false-positive routing on
ordinary prose containing the word "action"; and scope item 1's "no
core-api changes at all" didn't hold exactly — `services/core-api/app/
schemas/{logs,schedules}.py` moved to `libs/shared/src/aria_shared/
schemas/` so both `core-api` and the new MCP tools share one schema
definition. The `mcp-server` compose service runs behind a `profiles:
["mcp"]` gate (not started by a bare `docker compose up`) and listens on
`8003`, not the plan's placeholder `8002` — an explicit opt-in consistent
with strict decoupling: nothing about M1–M7 depends on it being up.
137 `ai-service` pytest cases pass (`test_mcp_tools.py`, `test_mcp_server.py`
new; `test_agents.py`/`test_chat.py` extended for the propose/confirm/
reject/unclear paths and the resume branch).

**Known follow-up (not yet built, called out explicitly in the plan as
out of scope):** "web-based operations APIs" (the other half of the PRD's
Phase 6 line — e.g. a weather or parts-lookup API) has no concrete use
case yet; and the confirm/cancel gate only guards ARIA's own conversational
path — a third-party MCP client calling `create_log`/`create_schedule`
directly executes immediately (same trust boundary as calling `core-api`
directly today, since both require a valid session cookie). Revisit either
if a real need surfaces.

### M9 — Multi-household / multi-user accounts ✅
Today there's exactly one hardcoded household and one hardcoded user
(`core-api/app/seed.py`), and login is a single shared password that always
resolves to that same seeded "owner" — no signup, no invite flow, no way to
add a second person to a household. `User.role` and a `check_permission()`
enforcement seam already exist (`libs/auth`, see `scaling-debt.md` #5) but
are unused in practice since nobody else can log in and the permission
registry is empty. This also bundles the M4-era accepted debt that Chroma
retrieval isn't scoped by household — harmless with one household, a real
cross-household leak once a second one can exist.

- `core-api`: `POST /auth/signup` (create a new household + owner), a real
  per-user email+password login (replacing the single shared password), a
  link-based invite flow (`POST /households/invites` → shareable
  `/invite/{token}` → `POST /auth/accept-invite`) to add a member to an
  existing household, and the first concrete `PERMISSIONS` entry
  (hard-delete restricted to `owner`).
- `core-api`: a per-record sharing model — every entity/document gains a
  `shared_with` setting (the whole household, the default, or a specific
  subset of its members); logs/schedules inherit access from their parent
  entity rather than carrying their own. View/edit access follows sharing;
  delete stays owner-only regardless of it; the household owner always has
  full access to everything, sharing or not.
- `worker`/`ai-service`: chunk embeddings gain a `household_id` (plus a
  backfill for chunks embedded before this milestone), and Chroma retrieval
  filters by the requesting session's household — closing the M4 gap.
  (Chat's document grounding is scoped to *household*, not to per-document
  sharing — a deliberate, called-out narrower scope than the CRUD side.)
- `frontend`: signup page, accept-invite page, a "Household members"
  section on the profile page (owner-only invite/revoke controls), and a
  sharing picker on the entity/document forms.

**Exit criteria:** sign up a second, independent household; confirm its chat
never surfaces the first household's documents (and vice versa); invite a
member into it via a shareable link; confirm a member is blocked from
hard-deleting a record while the owner isn't; within one household, confirm
an entity shared with only specific members is invisible to a member left
out of it, while the owner can still see and manage it regardless.

**Done as of 2026-07-20.** Landed per
`docs/plans/m9-multi-household-accounts.md`, matching its scope closely —
`libs/auth` gained `passwords.py` (stdlib `pbkdf2_hmac`, no new dependency)
and `sharing.py` (`has_shared_access`); `core-api` gained real signup/
email-password login, `routers/households.py` (invites + members +
accept-invite), sharing enforcement threaded through
`entities.py`/`logs.py`/`schedules.py`/`documents.py` via a shared
`require_entity_access`/`validate_shared_with` pair in `dependencies.py`,
and `PERMISSIONS[(None, "delete")] = {"owner"}`; `worker` writes
`household_id` into new chunk metadata and gained a one-off
`backfill_household_id.py` script; `ai-service` gained
`core_api_client.get_current_household_id()` and threaded `household_id`
through `retrieval.py` and every `agents/nodes.py` call site via
`config["configurable"]`, same never-checkpointed channel the cookie
already used; `frontend` gained `SignupPage`/`AcceptInvitePage`, a
`SharingControl` reused by `EntityForm`/`DocumentUploadForm`, and a
household-members card on the profile page.

**Real bug caught live, not by the test suite:** verifying against the
already-running dev stack (persistent Mongo volume, seeded days before this
milestone), logging in 500'd — `KeyError: 'password_hash'`, since
`ensure_seed_household` only inserts a user if none exists yet and never
retroactively patches an already-seeded household from before this field
existed. Fixed at the root: `verify_password()` now treats a `None`/missing
stored hash as "fails to verify" (widened its except clause to catch
`AttributeError`, not just `ValueError`) rather than assuming a string is
always passed in, and `routers/auth.py`'s `login()` reads it via `.get()`
instead of `[]`. Every mongomock-backed test starts from a freshly seeded
DB every time, so this exact gap — a real, persistent, previously-seeded
household — could only ever surface against genuinely persistent state, not
a fresh-fixture test run. Added a regression test at both layers
(`libs/auth/tests/test_passwords.py`, `core-api/tests/test_auth.py`) and
manually patched the running dev DB's seeded user with a real
`password_hash` so the existing default credentials keep working.

Verified end-to-end against the real running stack (all 6 services,
including `ollama`): signed up a genuinely second household, confirmed a
distinct `household_id`; ran the full invite → accept-invite flow twice
(two members); created an entity narrowed to one specific member and
confirmed — live, not just in a unit test — the excluded member gets `404`
on both the direct fetch and the list endpoint while the owner sees it
regardless of not being listed; confirmed hard-delete 403s for a member
with view/edit access and 204s for the owner, while archive/restore stay
available to the member; uploaded a real PDF and confirmed via a direct
Chroma inspection that new chunks carry the correct `household_id`; ran
`backfill_household_id` against the stack's real pre-existing corpus (72
chunks, 71 missing the field) — 69 backfilled, re-run touched 0 (confirmed
idempotent), and the 2 left untouched were confirmed to be orphaned vectors
from already-deleted Mongo documents (a pre-existing, unrelated gap, not a
regression); proved actual cross-household Chroma isolation by querying
with each household's real id directly (`where={"household_id": ...}`) —
every returned chunk belonged to the queried household, zero cross-leakage,
and a bogus id returned zero results; confirmed a full `/chat` round trip
against the real agent graph completed normally. 281 tests pass across
`libs/auth` (22), `libs/shared` (1), `core-api` (100), `worker` (15), and
`ai-service` (143); `frontend` `lint`/`build` both clean. Not verified: an
actual browser click-through of the new signup/accept-invite pages and the
entity/document sharing picker — no browser tooling available in this
session, the same gap every AI-milestone plan since M3 has noted; verified
via the same API contracts the UI calls, plus a clean typecheck/build,
rather than a driven UI walkthrough.

**Second real bug caught by dogfooding, same day:** migrating a real
household's pre-existing entities/documents onto M9 (reassigning
`household_id` across `entities`/`logs`/`schedules`/`documents` plus their
Chroma chunk metadata, at the user's request) surfaced that *every* such
record — anything created before this milestone shipped, not just the one
seed user — has no `shared_with` key in its stored document at all. Every
sharing check that read it via direct dict access (`doc["shared_with"]`,
8 call sites across `dependencies.py`/`entities.py`/`documents.py`/
`schedules.py`) raised `KeyError`, 500ing on *any* operation against
pre-migration data: viewing an entity, listing its logs, uploading a
document, creating a log. `list_entities`'s member-facing `$or` filter had
the same gap the other direction — a missing field matched none of its
clauses, so a non-owner's list view would have silently dropped every
pre-migration entity rather than erroring. Fixed every call site to
`.get("shared_with", "household")` (added a `{"shared_with": {"$exists":
False}}` clause to the list query) — same defensive pattern
`aria_auth.session` already uses for a pre-role-tracking session's missing
`role` key — and added `core-api/tests/test_sharing_pre_migration.py` (7
cases covering get/list/update/archive/delete/log-create/schedule-create/
document endpoints, both as owner and as a member, all against an entity
with the field stripped out to simulate real pre-migration shape). Verified
live: every entity/log/schedule/document endpoint 200'd afterward across
all 10 (now 11) of the migrated household's real entities. Separately
(a data issue, not a code bug): chat wasn't grounding on the household's
vehicle because it was archived — traced to an accidental bulk-archive
alongside 5 leftover dev "Smoke Test..." fixtures, both from before this
session. Restored the real entity, deleted the 5 fixtures, confirmed chat
grounding recovered end-to-end.

Plan: `docs/plans/m9-multi-household-accounts.md`.

## Explicitly deferred past MVP (others)

- **PWA / offline background sync** — listed in the PRD's frontend stack but
  not required for any MVP exit criterion above; revisit once M1's UI is
  real enough to need offline support.

These aren't forgotten, just sequenced after MVP — don't pull them forward
without updating this document first.

---

## How to use this document

1. Work milestones in order — each one builds on the last (M2 needs M1's
   entities to attach documents to; M4 needs M2's embeddings; etc.).
2. Before starting a milestone, turn its bullet list into a proper sub-task
   plan (`EnterPlanMode`) scoped to the actual files/endpoints involved —
   this doc stays high-level on purpose.
3. When a milestone finishes, flip its status marker here and add a one-line
   note if the scope shifted from what's written above.
4. If new scope surfaces that doesn't fit an existing milestone, add it as a
   bullet under the right milestone (or a new one) rather than letting it
   live only in conversation history.
