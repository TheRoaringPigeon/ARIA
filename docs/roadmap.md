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

### M3 — AI Phase 1: Basic AI chat ⬜
PRD Phase 1. Direct, stateless conversation with the local `ollama` model —
no retrieval, no persistence.

- `ai-service`: `POST /chat` — forwards to Ollama, returns a completion.
- `frontend`: minimal chat UI.

**Exit criteria:** ask ARIA a general question, get a model response. No
grounding yet — this milestone just proves the request path end-to-end.

### M4 — AI Phase 2: Naive RAG ⬜
PRD Phase 2. Chat gets grounded in whatever M2 has embedded.

- `ai-service`: similarity search against Chroma for the incoming question,
  inject top-k chunks into the prompt window before calling Ollama.

**Exit criteria:** ask about something covered in an uploaded manual, get an
answer that reflects the document's actual content (even without a visible
citation yet).

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
