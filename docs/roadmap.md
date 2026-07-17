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

### M5 — AI Phase 3: Document citations ⬜
PRD Phase 3. Traceable referencing.

- `ai-service`: prompt/response shape carries page, paragraph, and source
  filename for each retrieved claim (resolved via Chroma chunk metadata →
  `documents` record, per `data-model.md` §8).
- `frontend`: render citations as clickable references back to the source
  document.

**Exit criteria:** an answer visibly cites "Water Heater Manual, p.4" and
clicking it shows the source.

### M6 — AI Phase 4: Streaming responses ⬜
PRD Phase 4. Polish pass on M3–M5, not new capability.

- `ai-service`: SSE endpoint for chat.
- `frontend`: render tokens as they arrive instead of waiting for the full
  response.

**This is the MVP finish line.**

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

## Explicitly deferred past MVP (PRD Phases 5–6)

- **Phase 5 — Multi-agent orchestration** (Maintenance Agent, Vehicle
  Specialist, Research Assistant coordinating via LangGraph).
- **Phase 6 — MCP integration** (safe agent execution over local/web APIs).
- **PWA / offline background sync** — listed in the PRD's frontend stack but
  not required for any MVP exit criterion above; revisit once M1's UI is
  real enough to need offline support.
- **Multi-household / multi-user account management.** Today there's exactly
  one hardcoded household and one hardcoded user (`core-api/app/seed.py`),
  and login is a single shared password that always resolves to that same
  seeded "owner" — no signup, no invite flow, no way to add a second person
  to a household. `User.role` and a `check_permission()` enforcement seam
  already exist (`libs/auth`, see `scaling-debt.md` #5) but are unused in
  practice since nobody else can log in and the permission registry is
  empty. Future work: `POST /households` (create), a real per-user signup/
  invite flow (replacing the single shared password), `POST /households/{id}/users`
  (add a member), and actual `PERMISSIONS` entries once there's a concrete
  owner-vs-member restriction to enforce. This is what turns the existing
  seam into a real feature rather than dead plumbing.

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
