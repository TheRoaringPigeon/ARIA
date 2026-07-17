# M5 — AI Phase 3: Document Citations: Implementation Plan

## Context

M1–M4 are done (`docs/roadmap.md`): full CRUD tracking, document ingestion
(OCR → chunk → embed → Chroma), stateless chat (M3), and naive RAG (M4) —
`ai-service/app/retrieval.py::retrieve_context()` already embeds the latest
user message, queries Chroma's `documents` collection, and returns a
`list[RetrievedChunk]` (`text`, `mongo_document_id`, `page_number`,
`chunk_index`, `section_header`, `distance`) that gets folded into the system
prompt by `routers/chat.py::build_system_prompt()`. The household-data
grounding fast-follow added a parallel, independent grounding path
(`entity_grounding.py`) that already established the pattern this milestone
reuses: forward the browser's session cookie into `ai-service`, call
`core-api` for household-scoped data, degrade to nothing on any failure.

M5 implements PRD Phase 3 per `docs/roadmap.md`: the chat response must carry
enough structured detail per retrieved chunk (filename, page, section) that
the frontend can render clickable citations back to the source document.
**Exit criteria (roadmap, unchanged):** an answer visibly cites "Water
Heater Manual, p.4" and clicking it shows the source.

**Strict decoupling implication:** `retrieve_context()` itself needs zero new
dependencies — it stays exactly as it is (no cookie, no core-api call), so
the *answer* an LLM gives remains gated only by Ollama/Chroma being up, same
as M4. What's new is a second, independent step — resolving each retrieved
chunk's `mongo_document_id` to a real filename via `core-api` — which is
purely additive: on any failure (no cookie, expired session, core-api down)
the chat reply itself is unaffected, it just ships with `citations: []`,
mirroring `entity_grounding.py`'s existing degrade-to-empty contract rather
than introducing a new failure mode.

---

## Key design decisions

**Citations are a structured response field, not parsed from the model's
prose.** `ChatResponse` gains `citations: list[Citation]`, populated
server-side by resolving `RetrievedChunk.mongo_document_id` against
`core-api`'s existing `GET /documents/{document_id}`. The frontend renders
these directly — it never tries to regex a filename/page back out of the
model's free-text answer. This sidesteps a real reliability problem: a local
model (see M3's `QwenAdapter` — already needed a `<think>` scrubber because
`qwen3:14b`'s output isn't perfectly disciplined) cannot be trusted to
consistently emit a parseable citation marker in every reply. Citing "what
was actually retrieved and handed to the model for this answer" is something
`ai-service` knows deterministically; it doesn't need the model's cooperation
to report it accurately.

**Citations describe what was retrieved for the question, not what the
model's prose happens to quote.** There's no reliable signal today for
"which of the top-k chunks did the model actually draw on" — Ollama's
`/api/chat` returns plain text, not per-claim attribution. Rather than build
fragile heuristics to guess that, every chunk that made it into the prompt's
"Relevant document excerpts" section produces a citation candidate. This is
the same "additive, not exact" posture M4 already takes with
`AI_SERVICE_RAG_TOP_K` chunks — some may end up irrelevant to the specific
answer, same as today's excerpt injection already risks.

**The prompt also gets the filename/page inline, best-effort, so the model
*can* mention it in prose — but the frontend never depends on that.** Once a
chunk's `(document_id, page_number)` resolves to a real filename, the
excerpt line in `build_system_prompt()`'s "Relevant document excerpts"
section is prefixed with `(Source: {filename}, p.{page_number})` instead of
being bare chunk text. If resolution fails or there's no cookie, the excerpt
renders exactly as it does today (bare text, no prefix) — this is a strict
superset of M4's existing prompt shape, never a regression. The frontend's
clickable citation list is built from the structured `citations` field
regardless of whether the model's prose actually names the source, so the
exit criterion ("an answer visibly cites...") is satisfied by the rendered
UI even on the (likely, for a small local model) occasions where the prose
itself doesn't quote the parenthetical verbatim.

**New `ai-service/app/citations.py`, not more logic stuffed into
`retrieval.py` or `chat.py`.** Mirrors the existing module boundary:
`retrieval.py` only ever talks to Ollama/Chroma; `entity_grounding.py` only
ever talks to `core_api_client`. `citations.py` follows `entity_grounding.py`'s
shape exactly — cookie-gated, per-item failure isolation via
`asyncio.gather`, degrade to `[]` on any failure — but resolves *documents*
instead of *entities*. Keeping it a third sibling module (not folded into
`retrieval.py`) keeps `retrieve_context()`'s contract unchanged: it still
has zero core-api dependency, exactly as `docs/plans/m3-basic-ai-chat.md`
and the M4 entry above documented as deliberate.

**Dedup by `(document_id, page_number)`, not by chunk.** `AI_SERVICE_RAG_TOP_K`
(default 4) chunks can easily include two chunks from the same page (a page
that got split across the ~1000-char `CHUNK_FLUSH_THRESHOLD` in
`worker/app/logic/chunking.py`) or the same document on different pages. A
citation is meant to read like "Water Heater Manual, p.4" once, not twice —
so `resolve_citations()` walks the (already distance-sorted) chunk list and
keeps the first chunk seen per `(document_id, page_number)` pair, preserving
retrieval-rank order. Document lookups themselves are further deduped to one
`core_api_client.get_document()` call per unique `document_id` (a manual
cited on 3 different pages still only costs one round-trip), same
N+1-acceptable shape `entity_grounding.py` already uses for per-entity
logs/schedules.

**No new "paragraph" tracking added to the chunking pipeline.** The roadmap
bullet says citations carry "page, paragraph, and source filename," but
`data-model.md` §8 and the actual Chroma metadata (`worker/app/tasks/process_document.py`)
only ever tracked `page_number` + `chunk_index` + optional `section_header` —
there has never been a separate paragraph offset, and adding one means
touching M2's OCR/chunking logic, which is out of scope for a citations
milestone. `Citation` exposes `page_number` and `section_header` (when
present) — "Water Heater Manual, p.4" is satisfied by `page_number` alone;
`section_header` is a bonus when the chunk happens to carry one. Revisit
only if page-level granularity turns out to be too coarse in practice.

**A citation's "view source" link reuses `downloadUrl()`, not a new document
detail page.** There is no `/documents/:id` route today — documents are only
ever rendered inline inside `EntityDetailPage.tsx` via `DocumentList.tsx`,
and a `Document` can belong to multiple entities (`entity_ids` is
many-to-many per `data-model.md` §6), so there's no single canonical entity
page to route a citation to anyway. `Citation.document_id` feeds directly
into the same `downloadUrl(id)` (`src/api/documents.ts`, already used by
`DocumentList`'s existing "Download" link) opened in a new tab — clicking a
citation shows the source exactly per the exit criterion, without inventing
a new page this milestone doesn't need.

**Citations never travel through the resent conversation history — this is
the one correctness trap in this plan.** `ChatPage.tsx` (per M3) resends the
*entire* `messages: ChatMessage[]` array on every turn, and
`ChatRequest.messages` on the `ai-service` side is `ConfigDict(extra="forbid")`
— any unexpected key on a re-sent message is a `422`. If `ChatMessage` grew a
`citations` field and the frontend serialized history messages as-is, the
second assistant turn's request would carry the first turn's citations right
back to `ai-service` and get rejected. So `ChatMessage` (`api/chat.ts`) is
**not** changed — it stays exactly `{role, content}`, the wire type, forever.
`sendChatMessage()`'s return type changes instead, from `Promise<ChatMessage>`
to `Promise<{message: ChatMessage; citations: ChatCitation[]}>`, and
`ChatPage.tsx` keeps citations in a separate, UI-only structure
(`citationsByIndex: ChatCitation[][]`, indexed to position in `messages`)
that is read by `ChatBubble` for rendering but never fed back into the
`messages` array that gets serialized and resent.

**No docker-compose or config changes.** Everything routes through
`ai-service`'s existing `core_api_url` setting (already used by
`core_api_client.py` since the household-grounding fast-follow) and the
existing `GET /documents/{document_id}` endpoint — no new core-api routes,
no new env vars.

---

## File-by-file plan

### `services/ai-service/app/schemas/chat.py`
- Add `Citation(BaseModel)`: `document_id: str`, `filename: str`,
  `page_number: int`, `section_header: str | None = None`.
  `ConfigDict(extra="forbid")` for consistency with the other schemas here.
- `ChatResponse` gains `citations: list[Citation] = []`.

### `services/ai-service/app/core_api_client.py`
- Add `_get_one(path: str, cookie: str) -> dict` — same shape as `_get` but
  returns the parsed single-object body (`_get` is typed `-> list[dict]` and
  is only ever called against list endpoints today; a sibling helper is
  simpler than loosening `_get`'s return type for its two existing callers).
- Add `async def get_document(cookie: str, document_id: str) -> dict:
  return await _get_one(f"/documents/{document_id}", cookie)` — calls the
  existing, already-household-scoped `GET /documents/{document_id}`
  (`core-api/app/routers/documents.py::get_document`, unchanged).

### `services/ai-service/app/citations.py` (new)
- `async def resolve_citations(cookie: str | None, chunks: list[retrieval.RetrievedChunk]) -> list[Citation]:`
  - Returns `[]` immediately if `cookie is None` or `chunks` is empty —
    same cookie-required gate `entity_grounding.gather_entity_context` uses.
  - Walks `chunks` in order, keeping the first chunk seen per
    `(mongo_document_id, page_number)` key, to get an ordered, deduped list
    of citation candidates (preserves Chroma's distance-ascending order).
  - Collects the unique `mongo_document_id`s across those candidates and
    resolves each via `core_api_client.get_document(cookie, doc_id)` in
    parallel (`asyncio.gather`), through a `_safe_get_document` wrapper
    (same isolation pattern as `entity_grounding._safe_build_entity_context`:
    catch, `logger.warning` with the document id, return `None` — one 404
    (deleted document, or a cross-household Chroma leak per M4's known
    follow-up) doesn't drop every other citation) — one call per unique
    document, not per candidate.
  - Builds the final `list[Citation]` only for candidates whose document
    resolved, using `document["original_filename"]` for `filename`.
- Also exposes `_source_label` — actually, no: filename-prefixing for the
  prompt is built directly from the returned `Citation` list in
  `routers/chat.py` (a `{(document_id, page_number): filename}` lookup built
  from it), so `citations.py` has exactly one public function and no other
  ai-service module needs to know about the dedup/lookup shape.

### `services/ai-service/app/routers/chat.py`
- Import `citations`.
- After the existing `chunks, entity_context = await asyncio.gather(...)`
  (or the `query is None` branch, which now also sets `citation_list = []`),
  add `citation_list = await citations.resolve_citations(session_cookie, chunks)`.
  Sequential, not folded into the same `gather`, since it depends on
  `chunks` having already resolved.
- `build_system_prompt(chunks, entity_context, citation_list)` — new third
  param. Build `sources = {(c.document_id, c.page_number): c.filename for c in citation_list}`
  and change the excerpt-rendering line from `f"- {chunk.text}"` to prefix
  `f"(Source: {sources[key]}, p.{chunk.page_number}) "` in front of
  `chunk.text` when `(chunk.mongo_document_id, chunk.page_number)` is in
  `sources`, else the existing bare-text line — a strict superset of M4's
  current rendering.
- `ChatResponse(message=..., citations=citation_list)` on the return path.

### `services/ai-service/tests/test_core_api_client.py`
- New case: `get_document(cookie, id)` hits `GET /documents/{id}` with the
  session cookie and returns the parsed dict, following the existing
  `FakeAsyncClient`/`FakeResponse` pattern already used for
  `list_entities`/etc.

### `services/ai-service/tests/test_citations.py` (new)
- Following `test_retrieval.py`/`test_core_api_client.py`'s conventions
  (monkeypatch `core_api_client.get_document`):
  - No cookie → `[]`, no `get_document` calls made.
  - Empty `chunks` → `[]`.
  - Two chunks, same `(document_id, page_number)` → one citation, one
    `get_document` call.
  - Two chunks, same `document_id` different `page_number` → two citations,
    still only one `get_document` call (dedup by document, not just by
    citation key).
  - One `get_document` call raising (404/`httpx.HTTPError`) among several →
    that citation dropped, the others still present (failure isolation).
  - Citation order matches the chunks' original (distance-ascending) order.

### `services/ai-service/tests/test_chat.py`
- Extend the existing `retrieval_module`/`entity_grounding_module`
  monkeypatch fixtures with a `citations_module.resolve_citations` patch.
- New assertions: `resp.json()["citations"]` matches the patched
  `resolve_citations` return when chunks are present; `[]` when there are
  none; the system-prompt excerpt line carries the `(Source: ...)` prefix
  when a chunk's key is present in the resolved citations, and the existing
  bare-text assertion still holds when it isn't (covers the "resolution
  failed/no cookie" case without a real failure — `resolve_citations` just
  returns `[]` in that path already, per its own tests above).

### `services/frontend/src/api/chat.ts`
- Add `export interface ChatCitation { document_id: string; filename: string; page_number: number; section_header: string | null }`.
- `ChatMessage` is **unchanged** — stays `{role, content}` (see the "wire
  type" design decision above).
- `sendChatMessage`'s return type changes from `Promise<ChatMessage>` to
  `Promise<{ message: ChatMessage; citations: ChatCitation[] }>` — returns
  `{ message: body.message, citations: body.citations ?? [] }`.

### `services/frontend/src/hooks/useSendChatMessage.ts`
- No structural change beyond the type flowing through from `api.sendChatMessage`
  — `useMutation`'s inferred success type becomes
  `{message: ChatMessage; citations: ChatCitation[]}` automatically.

### `services/frontend/src/components/ChatBubble.tsx`
- New optional prop: `citations?: ChatCitation[]`.
- When `citations` is non-empty and `message.role === 'assistant'`, render a
  small "Sources" row below the `ReactMarkdown` content: one link per
  citation, `<a href={downloadUrl(citation.document_id)} target="_blank" rel="noopener noreferrer">`,
  label `` `${citation.filename}, p.${citation.page_number}` `` (import
  `downloadUrl` from `../api/documents`) — same anchor pattern
  `DocumentList.tsx` already uses for its own "Download" link, just styled
  as a small chip/pill row rather than a full-width list item.

### `services/frontend/src/pages/ChatPage.tsx`
- Keep the existing `messages: ChatMessage[]` state exactly as-is (this is
  what gets resent on every turn — must stay `{role, content}` only).
- Add `citationsByIndex: ChatCitation[][]` state (or a plain object keyed
  by index), same length-tracking as `messages`.
- On a successful send: append the assistant's `ChatMessage` to `messages`
  as today, and set `citationsByIndex[messages.length] = result.citations`
  (the index the new assistant message lands at) in the same update.
- Pass `citations={citationsByIndex[index]}` into `<ChatBubble>` alongside
  `message={m}` in the `messages.map(...)` render.

---

## Sequencing

1. `ai-service`: `schemas/chat.py` (`Citation`, `ChatResponse.citations`),
   `core_api_client.py::get_document`, `citations.py`, wire both into
   `routers/chat.py` (including the prompt-prefix change), plus
   `test_citations.py`/updated `test_core_api_client.py`/`test_chat.py`.
   Verify against the running `docker-compose` stack with a real embedded
   document and a real logged-in session cookie before touching the
   frontend — confirm `POST /chat`'s JSON body now has a non-empty
   `citations` array for a question that matches an uploaded manual.
2. `frontend`: `api/chat.ts` (`ChatCitation`, `sendChatMessage` return
   shape), `ChatBubble.tsx` (Sources row), `ChatPage.tsx`
   (`citationsByIndex` wiring). Confirm `npm run lint`/`build` clean before
   the manual walkthrough.
3. Manual walkthrough end-to-end (below), including the strict-decoupling
   checks for the new citation-resolution path specifically (it's the one
   piece of this milestone with a new core-api dependency).

---

## Verification

**Manual walkthrough:** log in, upload a manual (or reuse one still
embedded from M2/M4 testing), ask a question it answers. Confirm the reply
renders one or more "Sources" links below the message, each labeled
`{filename}, p.{page}`, and confirm clicking one downloads/opens the exact
source PDF. Ask a follow-up in the same conversation (second assistant
turn) and confirm the request still succeeds — this is the concrete check
against the "citations leak into resent history and 422" trap the design
section calls out; inspect the outgoing request body (browser devtools) to
confirm resent messages carry only `{role, content}`.

Then the strict-decoupling checks, each confirming the *chat answer* is
unaffected while only `citations` degrades to `[]`:
- No session cookie (open `ai-service` directly or clear cookies): grounded
  answer still returns (M4 behavior unchanged, since `retrieve_context` has
  no cookie dependency), `citations: []`.
- Stop `core-api` mid-session: chat still answers using the already-running
  `ollama`/`chromadb`, `citations: []`, a `logger.warning` in `ai-service`'s
  logs; restart `core-api` and confirm citations resume with no `ai-service`
  restart needed (mirrors the existing entity-grounding decoupling check).
- Stop `chromadb`: falls back to ungrounded (M3-style) chat exactly as
  today, `citations: []` (no chunks to cite in the first place).

**Automated:** `uv run pytest` in `ai-service` (new `test_citations.py` +
extended `test_core_api_client.py`/`test_chat.py`); `npm run lint` +
`npm run build` in `frontend`.

### Critical files
- `services/ai-service/app/citations.py` (new)
- `services/ai-service/app/core_api_client.py`
- `services/ai-service/app/routers/chat.py`
- `services/ai-service/app/schemas/chat.py`
- `services/frontend/src/api/chat.ts`
- `services/frontend/src/components/ChatBubble.tsx`
- `services/frontend/src/pages/ChatPage.tsx`
