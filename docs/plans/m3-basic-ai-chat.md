# M3 — AI Phase 1: Basic AI Chat: Implementation Plan

## Context

M1 and M2 are done (`docs/roadmap.md`): full CRUD tracking across all 5
domains, real session auth, and a document ingestion pipeline that OCRs,
chunks, and embeds uploads into Chroma. Nothing in `ai-service` is called
from outside itself yet — `app/ollama.py` and `app/chroma.py` exist only to
back the `/health` probe.

M3 implements PRD Phase 1 per `docs/roadmap.md`: a direct, stateless
conversation with the local Ollama model. No retrieval (that's M4), no
persistence (that's not planned at all for M3 — chat history lives only in
the browser tab). **Exit criteria (from the roadmap, unchanged):** ask ARIA
a general question, get a model response — this milestone just proves the
request path end-to-end.

**Strict decoupling implication for this milestone:** `ai-service` gains its
first real endpoint, but not a Mongo connection or `aria-auth` dependency —
it stays exactly as isolated from `core-api`'s database as it is today. A
household member must still be able to fully use entity/log/schedule
tracking with `ai-service`/`chromadb`/`ollama` stopped, and the reverse must
also hold: bringing `ai-service` up must not require touching Mongo.

---

## Key design decisions

**`POST /chat` is unauthenticated at the service level — a deliberate,
flagged scope decision, not an oversight.** `ai-service` has no Mongo
connection, no `aria-auth` dependency, and no session concept today (see
`app/main.py`, `app/config.py` — nothing there resembles `core-api`'s
`db.py`/`dependencies.py`). Wiring up `build_get_current_session` would mean
giving `ai-service` its own Motor client purely to validate a cookie it does
nothing else with — a real structural change that belongs to whichever
milestone first needs `ai-service` to know *which household* it's serving
(M4's retrieval needs to scope Chroma queries to a household's documents
anyway, so that's the natural point to revisit this). For M3, the frontend's
existing `RequireAuth` wrapper is the only gate: the chat page is unreachable
without a logged-in session, but the `ai-service` port itself has none if hit
directly. Acceptable for a locally-hosted, single-household MVP; noted here
so it isn't forgotten by M4/M5.

**Chat message schemas live in `ai-service`, not `aria_shared`.** Everything
in `aria_shared` today is a Mongo-persisted data contract (entities, logs,
schedules, documents). Chat messages are never written to Mongo in M3 — they
exist only for the duration of one request — so they don't belong in the
shared data-contract library. New `app/schemas/chat.py` local to
`ai-service`, same reasoning `docs/plans/m2-document-ingestion-hub.md` used
for worker's independent Ollama/Chroma clients: shared *data*, not shared
*runtime* types that happen to not be data yet.

**The system prompt is injected server-side, and the client can't send a
`system` role.** `ChatMessage.role` is `Literal["user", "assistant"]` —
`ai-service` prepends its own fixed system message (identifies the
assistant as ARIA, notes it currently has no access to the household's own
data) before forwarding to Ollama. This is a minimal persona pass, not
prompt engineering scope creep: without it, "ask ARIA a question" would get
a response from a bare, unbranded model. Rejecting a client-supplied
`system` role keeps that persona from being overridable from the frontend.

**Chat history lives only in React state — no localStorage, no backend
persistence.** The client holds the running `messages` array and resends the
whole thing on every send (matching Ollama's own stateless `/api/chat`
shape, which `app/ollama.py::chat()` already forwards to as-is, unchanged).
Navigating away from the chat page or reloading clears it. This is the
literal reading of the roadmap's "no persistence" — if it turns out
households want history to survive a reload, that's a small, separate
addition (e.g. localStorage) that doesn't change the "no backend
persistence" guarantee; not doing it now keeps M3 scoped to proving the
request path.

**Response normalization goes through a swappable model adapter, not an
inline string strip in the router.** `qwen3:14b` (the default model) prefixes
every reply with a `<think>...</think>` reasoning block that the UI should
never show. Rather than special-casing that in `routers/chat.py`, `app/adapters/`
defines a `ModelAdapter` ABC (`normalize_response(content: str) -> str`), a
`QwenAdapter` implementation, and a small name → class registry selected by
a new `AI_SERVICE_MODEL_ADAPTER` setting (default `"qwen"`, set explicitly
in `docker-compose.yml` next to `AI_SERVICE_OLLAMA_MODEL` so the two are
changed together). `get_adapter()` is a lazy module-level singleton, the
same idiom as `ollama.get_client()`/`chroma.get_client()` — not FastAPI
`Depends()`, since nothing else in `ai-service` uses that pattern yet and
this doesn't need per-request state. Swapping to a different local model
later means adding a new adapter class and flipping one env var, not
touching the router.

**No docker-compose changes** beyond the one new `AI_SERVICE_MODEL_ADAPTER`
env var above. `ai-service` already runs on `8001`, already
has `AI_SERVICE_OLLAMA_HOST`/`AI_SERVICE_OLLAMA_MODEL` wired, and the
frontend already reads `VITE_AI_SERVICE_URL` (used today by `HealthPage`).
`add_permissive_cors(app)`'s wildcard-origin/no-credentials default is
exactly right for an unauthenticated, cookie-less endpoint — nothing to
change there either.

**A new frontend API module, not a generalized `apiFetch`.** `src/api/client.ts`'s
`apiFetch` hardcodes `CORE_API_URL` and always sends `credentials: 'include'`.
`ai-service` needs neither (no cookie to send, and its CORS is
credentials-less anyway), so `src/api/chat.ts` gets its own small `fetch`
wrapper against `VITE_AI_SERVICE_URL`, mirroring `apiFetch`'s error-parsing
shape (`AiServiceError(status, message)` from `{detail}`) rather than
generalizing `client.ts` to multiple base URLs for a single caller.

**Assistant replies render as Markdown, user messages don't.** Local models
routinely reply with Markdown (bold, numbered/bulleted lists) — rendering it
as plain `whitespace-pre-wrap` text left literal `**`/`-`/`1.` characters
visible, which reads as broken rather than minimal. `ChatBubble` renders the
assistant side through `react-markdown` (new dependency) with a small
`components` override for spacing/list styling consistent with the app's
existing theme tokens. Deliberately **no `rehype-raw` plugin** — the
assistant's content is untrusted, model-generated text, and without
`rehype-raw` `react-markdown` only ever interprets Markdown syntax, never
raw HTML tags, so there's no XSS surface from a model response. The user's
own message stays plain text (`whitespace-pre-wrap` on a `<p>`) — no reason
to Markdown-parse what the user just typed.

**Timeouts/errors surface as a friendly inline state, not a crash.**
`app/ollama.py`'s `httpx.AsyncClient` already has a 300s timeout (local
`qwen3:14b` can be slow, especially cold-loading into memory on the first
request). The router catches `httpx.HTTPError` and re-raises as a `502` with
a plain-language detail; the frontend renders that inline in the chat thread
next to the unanswered message rather than losing it, and leaves the user's
message in place so they can retry.

---

## File-by-file plan

### `services/ai-service/pyproject.toml`
- Add a `[dependency-groups] dev` section: `pytest>=8.3`, `pytest-asyncio>=0.24`,
  `httpx>=0.27` (already a main dependency, but needed by `TestClient` too).
  Add `[tool.pytest.ini_options] asyncio_mode = "auto"`. `ai-service` has no
  tests today — this is the first test setup for the service.

### `services/ai-service/app/schemas/__init__.py`, `chat.py` (new)
- `ChatMessage(BaseModel)`: `role: Literal["user", "assistant"]`, `content: str`
  (non-empty). `ChatRequest(BaseModel)`: `messages: list[ChatMessage]`
  (`min_length=1`), `ConfigDict(extra="forbid")`. `ChatResponse(BaseModel)`:
  `message: ChatMessage`.

### `services/ai-service/app/adapters/base.py`, `qwen.py`, `__init__.py` (new)
- `ModelAdapter` ABC with `normalize_response(content: str) -> str`.
- `QwenAdapter` strips a leading `<think>...</think>` block via regex;
  falls back to the raw (stripped) content if nothing survives stripping,
  so a cut-off/malformed think block can't produce an empty message.
- `__init__.py`: `_ADAPTERS` name → class registry, lazy `get_adapter()`
  singleton reading `settings.model_adapter`, raises `ValueError` on an
  unknown adapter name.

### `services/ai-service/app/config.py`
- Add `model_adapter: str = "qwen"`.

### `services/ai-service/app/routers/chat.py` (new)
- `SYSTEM_PROMPT` constant (short, identifies ARIA, notes no data access yet).
- `POST /chat`: build `[{"role": "system", "content": SYSTEM_PROMPT}, *[m.model_dump() for m in request.messages]]`,
  call `await ollama.chat(messages=...)`, run the raw content through
  `get_adapter().normalize_response(...)`, then build `ChatResponse` from
  the normalized content. Wrap the `ollama.chat()` call in
  `try/except httpx.HTTPError` → `HTTPException(502, detail="ai-service could not reach the local model")`.

### `services/ai-service/app/main.py`
- Import and `include_router(chat.router)` alongside the existing health router.

### `services/ai-service/tests/__init__.py`, `conftest.py`, `test_chat.py`, `test_adapters.py` (new)
- No DB/session fixtures needed (unauthenticated, no Mongo) — just a plain
  `TestClient(app)` fixture.
- `test_chat.py`: monkeypatch `app.ollama.chat` to return a canned
  `{"message": {"role": "assistant", "content": "<think>...</think>\n\n..."}}`
  dict and assert `POST /chat` strips the think block and echoes the clean
  content back, and that the system prompt was prepended (assert on the
  mock's call args); empty `messages` list → `422`; a client-supplied
  `role: "system"` message → `422`; `ollama.chat` raising `httpx.HTTPError`
  → `502`.
- `test_adapters.py`: `QwenAdapter.normalize_response` — strips a
  well-formed think block, passes through content with none, falls back to
  raw content when stripping would leave nothing; `get_adapter()` returns a
  `QwenAdapter` by default and raises `ValueError` on an unknown
  `model_adapter` setting.

### `services/frontend/src/api/chat.ts` (new)
- `AI_SERVICE_URL = import.meta.env.VITE_AI_SERVICE_URL ?? 'http://localhost:8001'`
  (same fallback `HealthPage.tsx` already uses).
- `AiServiceError` class mirroring `ApiError`'s shape.
- `sendChatMessage(messages: ChatMessage[]): Promise<ChatMessage>` — `fetch`
  (no `credentials`), POSTs `{messages}` to `/chat`, throws `AiServiceError`
  parsed from `{detail}` on non-2xx, returns `body.message`.
- `ChatMessage` type (`{role: 'user' | 'assistant'; content: string}`),
  colocated here since nothing else needs it yet.

### `services/frontend/src/hooks/useSendChatMessage.ts` (new)
- `useMutation({ mutationFn: (messages: ChatMessage[]) => api.sendChatMessage(messages) })` —
  no query invalidation (nothing cached), matching the one-hook-per-operation
  convention `useUploadDocument.ts` follows.

### `services/frontend/src/components/ChatBubble.tsx` (new)
- Small presentational component: role-based alignment/styling (user vs.
  assistant) using the existing theme-token classes, same spirit as
  `StatusBadge.tsx`. Assistant content renders through `react-markdown`
  (`components` override for `p`/`ul`/`ol`/`li`/`strong`/`code` spacing);
  user content stays a plain `whitespace-pre-wrap` paragraph.

### `services/frontend/package.json`
- Add `react-markdown` (new dependency — no Markdown rendering existed
  anywhere in the frontend before this).

### `services/frontend/src/pages/ChatPage.tsx` (new)
- Local `useState<ChatMessage[]>` for the running conversation, local
  `useState<string>` for the input box.
- On send: append the user message optimistically, call
  `useSendChatMessage()`'s `mutate`, append the assistant reply on success;
  on error, render the error inline (via `AiServiceError` check, same
  `error instanceof X ? error.message : null` pattern `EntityDetailPage.tsx`
  uses) without dropping the pending user message, so retry just re-sends.
  Disable the input/send button while `isPending`; show a lightweight
  "thinking…" indicator in place of the next bubble.
- Renders the message list via `ChatBubble`, auto-scrolls to the latest
  message.

### `services/frontend/src/App.tsx`
- Add `<Route path="/chat" element={<ChatPage />} />` as a sibling inside
  the existing authenticated `<Route>` block (so `RequireAuth` covers it,
  same as every other feature page).

### `services/frontend/src/components/Layout.tsx`
- Add a `<NavLink to="/chat">Chat</NavLink>` entry alongside the existing
  Entities / What's Due / Health links.

---

## Sequencing

1. `ai-service`: `schemas/chat.py`, `routers/chat.py`, wire into `main.py`,
   plus `tests/`. Verify in isolation via `/docs` or `curl -X POST
   localhost:8001/chat -d '{"messages":[{"role":"user","content":"hi"}]}'`
   against the running `docker-compose` stack — confirm a real model
   response comes back before touching the frontend at all.
2. `frontend`: `api/chat.ts`, `hooks/useSendChatMessage.ts` — no UI yet,
   just confirm the module compiles/types check.
3. `frontend`: `ChatBubble.tsx`, `ChatPage.tsx`, then wire the route into
   `App.tsx` and the nav link into `Layout.tsx`.
4. Manual walkthrough end-to-end (below), including the strict-decoupling
   check.

---

## Verification

**Manual walkthrough:** log in, click "Chat" in the nav, ask a general
question ("what's a good rule of thumb for oil change intervals?"), confirm
a real model response renders in a reasonable time. Refresh the page and
confirm the conversation is gone (no persistence, as designed). Ask a
follow-up in the same session and confirm the model has the prior turn as
context (proves history round-trips through the request, not just single-shot).
Then the strict-decoupling checks, both directions: stop `ai-service`/`ollama`
and confirm Entities/Due Soon/Health/etc. are entirely unaffected on
`core-api`, and that the Chat page itself shows the friendly `502` error
state rather than an unhandled crash; separately, confirm `ai-service` was
never touching Mongo in the first place by inspecting `app/main.py`/`db.py`
(there is no `db.py`) rather than by stopping Mongo (chat has no Mongo
dependency to lose).

**Automated:** `uv run pytest` in `ai-service` (new — first test suite for
this service); `npm run lint` + `npm run build` in `frontend`.

### Critical files
- `services/ai-service/app/routers/chat.py`
- `services/ai-service/app/schemas/chat.py`
- `services/ai-service/app/adapters/qwen.py`
- `services/ai-service/app/ollama.py` (reused unchanged — confirm the
  `/api/chat` shape still matches)
- `services/frontend/src/api/chat.ts`
- `services/frontend/src/pages/ChatPage.tsx`
- `services/frontend/src/App.tsx`
- `services/frontend/src/components/Layout.tsx`
