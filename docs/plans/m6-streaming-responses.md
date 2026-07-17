# M6 — AI Phase 4: Streaming Responses: Implementation Plan

## Context

M1–M5 are done (`docs/roadmap.md` — M5's status marker was stale until this
plan's prep work caught it; the citations milestone actually shipped in
commits `62384ae`/`ba8ef7d`). Today's `/chat` request path
(`ai-service/app/routers/chat.py::chat()`) is fully synchronous end to end:
resolve retrieval chunks (`retrieval.py`) and entity grounding
(`entity_grounding.py`) in parallel, resolve citations from the chunks
(`citations.py`), build one system prompt (`build_system_prompt()`), then a
single blocking `ollama.chat(messages, stream=False)` call that waits for
the *entire* completion before FastAPI returns one `ChatResponse` JSON body.
The frontend (`ChatPage.tsx`) shows a static "Thinking…" bubble for the
whole request, then swaps in the full message at once.

M6 implements PRD Phase 4 per `docs/roadmap.md`: **"a polish pass on M3–M5,
not new capability."** Nothing about *what* gets retrieved, grounded, or
cited changes — only how the model's answer travels from Ollama to the
browser. **Exit criteria (roadmap, unchanged):** render tokens as they
arrive instead of waiting for the full response.

**Why this is the MVP finish line:** M1–M5 already deliver every functional
promise in the PRD (CRUD tracking, ingestion, grounded + cited chat). M6 is
pure latency/UX polish on top of a feature set that's otherwise complete —
once it lands, `docs/roadmap.md`'s "Definition of MVP" section is fully
satisfied.

**Strict decoupling implication:** identical to M3–M5. Retrieval, entity
grounding, and citation resolution are unchanged — they still run to
completion *before* the model is ever called, still degrade to
empty/ungrounded on any failure (no cookie, `core-api` down, `chromadb`
down), independent of whether the model's answer streams or not. The only
new failure surface is Ollama becoming unreachable *after* the SSE response
has already started — see the error-framing decision below, since that one
case can no longer be reported as an HTTP status code.

---

## Key design decisions

**Convert `POST /chat` in place — no new `/chat/stream` endpoint.** M3, M4,
M5, and the household-grounding fast-follow each changed `/chat`'s behavior
without forking or versioning the route; `build_system_prompt()`'s
signature has already grown twice this way. Continuing that pattern (rather
than standing up a parallel endpoint) keeps retrieval, entity grounding,
citation resolution, and prompt construction as the single, unchanged
"gather everything, then call the model" path — M6 only replaces the last
step, `await ollama.chat(...)` → one JSON response, with a token stream.
There is exactly one `/chat` route, one frontend call site, and no
dead code left behind from an abandoned non-streaming path.

**Response shape: `text/event-stream` via four named SSE events —
`citations`, `thinking`, `token`, `error`.** No `done` event: the stream
simply ends (generator returns), and the frontend's `fetch` reader already
reports `done: true` when the body closes — an explicit terminal event
would be redundant state to keep in sync.
- `citations` is emitted exactly once, **first**, before any `thinking` or
  `token` event. Citations are already fully resolved (same
  `citations.resolve_citations()` call, unchanged) before Ollama is ever
  invoked — sending them immediately means the frontend can render the
  "Sources" row the instant the response starts, rather than waiting for
  the model to finish (or worse, buffering them until stream-end and
  re-introducing the exact latency this milestone removes).
- `thinking` carries the model's reasoning — the content that would sit
  inside `<think>...</think>` — one event per classified delta, rendered
  live in a **temporary** preview the frontend discards the moment the
  real answer starts (see the UX decision below; this is a deliberate
  product choice, not just a technical side effect of how the filter
  works).
- `token` carries the actual answer content, once the filter has passed
  the closing `</think>` (or determined there never was a think block at
  all) — potentially zero times if the model's entire response turns out
  to be an unterminated think block with nothing after it (rare edge case,
  handled by `flush()` below).
- `error` is emitted at most once, replacing today's `HTTPException(502)`
  for the two failure modes `ollama.chat()` can hit
  (`httpx.HTTPError` / malformed response body) — **only** reachable because
  SSE has already committed a `200` status by the time streaming starts
  (Starlette's `StreamingResponse` sends the response-start ASGI message
  before consuming the first item from the body iterator, so a mid-generator
  failure can no longer flip the status code). The frontend distinguishes
  this from an HTTP-level failure (still possible for the initial
  connection itself — `ai-service` unreachable entirely) by watching for an
  `error` frame in the stream, not by checking `res.ok`.

**Think-block content is classified and routed, not discarded — a UX
decision as much as a technical one.** Originally this plan had the filter
throw reasoning text away entirely; the design changed after discussing it
with the user, who wanted the model's live reasoning visible in a
*temporary* preview while it's happening (comparable to the "thinking"
panels some hosted chat UIs show), fully replaced by the real answer once
it starts — never persisted, never part of the saved conversation.
`qwen3:14b` prefixes essentially every reply with a `<think>...</think>`
reasoning block (sometimes hundreds of tokens); today's
`QwenAdapter.normalize_response()` regexes the *complete* string after the
fact and drops that block, but a per-delta version can't just check each
incoming piece in isolation — a tag like `<think>` or `</think>` can arrive
split across two, three, or more deltas, so a few characters always have to
be held back briefly just to find out which side of a tag boundary they're
on. That brief, unavoidable hold-back is the only "buffer" involved — it is
not an artificial delay added for effect, and the moment a chunk is
classified it is forwarded immediately.

`ModelAdapter` gains a second abstract method, `create_stream_filter() ->
StreamFilter`, where `StreamFilter` is a small stateful protocol:
- `feed(delta: str) -> list[tuple[Literal["thinking", "answer"], str]]` —
  returns zero, one, or two `(kind, text)` segments now safe to emit
  (two when a single delta happens to straddle the `</think>` boundary:
  the tail of the reasoning plus the start of the real answer in the same
  chunk); buffers internally otherwise.
- `flush() -> list[tuple[Literal["thinking", "answer"], str]]` — called
  once after the model signals `done: true`. If the filter is still
  sitting on unclassified buffer, it's surfaced rather than silently
  dropped — classified as `"thinking"` or `"answer"` depending on which
  state the filter was in (see below) — so a truncated response is never
  silently swallowed.

`QwenAdapter.create_stream_filter()`'s implementation is a three-state
machine:
1. **`detecting_open`** (initial) — accumulate every fed delta into a
   buffer, checking it against the literal `<think>`:
   - If the buffer is **not** a prefix of `<think>` (already diverged —
     the model didn't open with a think block at all): switch straight to
     `answering` and emit the entire buffered-so-far text as `"answer"`.
     This keeps a future non-reasoning model (behind a different adapter)
     from ever having its output delayed.
   - If the buffer exactly matches `<think>` with more text already
     following in the same delta: switch to `thinking`, emit that
     remainder as `"thinking"`.
   - Otherwise (still an unterminated, valid prefix of `<think>`): keep
     buffering, emit nothing yet.
2. **`thinking`** — accumulate into a small rolling buffer just large
   enough to catch a split `</think>` tag. Any prefix of the buffer that
   can no longer be part of `</think>` is emitted immediately as
   `"thinking"` (keeping only the still-ambiguous tail buffered — at most
   `len("</think>") - 1` characters). Once `</think>` is found: everything
   before it is emitted as `"thinking"`, switch to `answering`, and
   anything after the tag in the same buffer is emitted as `"answer"` in
   the same `feed()` call.
3. **`answering`** — every subsequent `feed()` call returns its input
   unchanged, tagged `"answer"`, immediately.

**One added polish detail, corrected after live testing:** Qwen typically
leaves a blank line between `</think>` and the real answer. An earlier
version of this filter `.lstrip()`ed only the remainder text captured at
the exact moment `</think>` was found in the same buffer — reasoning it'd
be rare for the boundary to split across chunks. Testing against the real
running stack (`docker-compose`, real `qwen3:14b`) immediately proved that
assumption wrong: Ollama streams roughly one token per delta, so the
closing tag and the blank line after it consistently arrive as *separate*
chunks — the first `token` frame in a real response was reliably a bare
`"\n\n"`. Fixed by tracking a `_suppress_leading_whitespace` flag that
persists across as many `"answering"`-state `feed()` calls as it takes to
see real content (`_emit_answer()`), not just the one call where the tag
was found — verified against the live stack afterward, first `token` frame
is now the real first word.

**`flush()`'s two outcomes depend on which state the filter was in when the
stream ended — this matters for getting the "unterminated think block"
edge case right.** An earlier draft of this plan had `flush()` reclassify
*all* buffered reasoning text as `"answer"` on an unterminated block, on
the theory that "if it never closed, treat it like plain text" — mirroring
`normalize_response()`'s own fallback too literally. That's wrong for a
*streaming* filter: reasoning text is released as `"thinking"` live,
incrementally, well before we know whether the block will ever close — by
the time the stream ends, only the last few genuinely-ambiguous characters
(the tail that might still have turned into `</think>`) are left
unclassified, and the bulk of the reasoning was already shown to the user
as `"thinking"` long ago. So:
- If the filter was in `thinking` state (confirmed inside a real think
  block, just hadn't seen the close tag yet): leftover buffer flushes as
  `"thinking"` — it's still reasoning text either way, we just weren't
  100% sure yet it wasn't the start of `</think>`.
- If the filter was still in `detecting_open` (never even confirmed the
  reply opened with `<think>` at all — e.g. cut off after just `<thi`):
  leftover flushes as `"answer"`, since nothing was ever confirmed to be
  reasoning in the first place.
- `answering` state never has anything buffered (it's pure passthrough),
  so `flush()` is always `[]` from that state.

**`ollama.py` gains `chat_stream()`, parsing Ollama's own NDJSON framing —
not touching the existing `chat()`.** Ollama's `/api/chat` with
`"stream": true` returns newline-delimited JSON objects
(`{"message": {"role": "assistant", "content": "<delta>"}, "done": bool,
...}`), one message-content delta per line — not SSE. `chat_stream()` is a
new `async def chat_stream(messages: list[dict]) -> AsyncIterator[dict]`
using `client.stream("POST", "/api/chat", json={...,"stream": True})` +
`resp.aiter_lines()`, `json.loads`-ing each non-blank line and yielding the
parsed dict, so `routers/chat.py` never touches raw NDJSON directly. The
existing non-streaming `chat()` is left untouched — nothing else in
`ai-service` calls it in a way that needs converting.

**No change to `retrieval.py`, `entity_grounding.py`, `citations.py`, or
`build_system_prompt()`'s construction logic.** All four still run exactly
as they do today, to completion, before the first byte of the SSE response
is written. This is deliberate, not an oversight: streaming *only* the
model's own output (versus, say, streaming retrieval progress too) is the
entire scope PRD Phase 4 asks for, and keeps every existing M4/M5
degrade-to-empty guarantee untouched.

**Frontend can't use `EventSource`.** `EventSource` only supports `GET` with
no custom request body — `/chat` needs a `POST` with a JSON `{messages}`
body and `credentials: 'include'` (the session cookie citations/entity
grounding depend on). So the frontend hand-rolls SSE framing over
`fetch()`'s `ReadableStream`: read chunks via `response.body.getReader()`,
decode with `TextDecoder`, accumulate into a buffer, split on `"\n\n"` frame
boundaries, and parse each frame's `event:`/`data:` lines. This is the
standard pattern for POST-based chat streaming (same shape most LLM chat
UIs use) — no new dependency needed for it.

**`useSendChatMessage` (react-query `useMutation`) is replaced by a plain
`useState`/`useRef`-based `useStreamChatMessage` hook, not adapted in
place.** A mutation's `onSuccess`/`onError` model fits a single
request→response round trip; it has no natural slot for "here's a token,
and here's another one, forty times." The replacement hook exposes
`{ send(messages, callbacks), isPending, error }` where `callbacks` is
`{ onCitations, onToken, onError }`, holds an `AbortController` in a ref so
navigating away from `ChatPage` (or firing a new send mid-stream) cancels
the in-flight reader, and manages `isPending`/`error` as local state
updated from those same callback sites — same externally-visible shape
`ChatPage.tsx` already reads today (`isPending`, `isError`, `error`), so the
loading spinner / error banner / retry button need no redesign.

**`ChatMessage`'s wire shape stays exactly `{role, content}` — unaffected by
this milestone.** M5 already established (and `ChatPage.tsx`'s
`toWireMessages()` already enforces) that conversation history resent to
`ai-service` never carries citations, because `ChatRequest.messages` is
`extra="forbid"`. Streaming doesn't change that contract — the frontend
still assembles one final `DisplayMessage` (`{role, content, citations}`)
once a turn completes, and still strips it back down to `{role, content}`
before resending history. What's new is *when* `content` is fully known:
today it's known all at once when the mutation resolves; after this
milestone it's built up incrementally across `token` events before the
stream ends.

**Streaming placeholder lifecycle in `ChatPage.tsx` — two separate UI
surfaces, one temporary and one permanent.** Today, sending shows a static
"Thinking…" indicator until the mutation resolves, then appends one
complete `DisplayMessage`. After this change there are two distinct
regions during a send:
- A **transient reasoning preview** — plain local state (`thinkingPreview:
  string`, not part of `messages`), appended to as `thinking` events
  arrive, rendered in place of the old static "Thinking…" text (dimmed/
  italic styling, clearly marked as ARIA's in-progress reasoning, not a
  final answer). This state is never added to `messages`, never resent,
  and never persisted — it exists purely so the user can watch the model
  "think" live, per the product decision above.
- The **real assistant message**, which does not exist in `messages` at
  all yet. The first `token` (answer) event is what creates it: at that
  point, `thinkingPreview` is cleared and a placeholder `DisplayMessage`
  (`{role: 'assistant', content: '', citations: []}`) is appended and its
  index captured. Every subsequent `token` event appends to that message's
  `content` via an index-matched functional `setMessages` update. A
  `citations` event always arrives before either kind of content (per the
  design above), so it's held in a ref until the placeholder is created,
  then attached to it at that point.

**On an `error` event:** clear `thinkingPreview`, and if the real message
placeholder was already created this turn, remove it from `messages`
entirely (not left dangling as a half-written bubble) — so that `retry()`,
which resends `toWireMessages(messages)` verbatim, reproduces exactly
today's invariant: a failed turn never leaves a partial assistant entry in
the history that gets resent. If the model was cut off *while still
reasoning* (never reached a real answer), the reasoning text itself is
just discarded with the preview — there is no "answer" to show, and per
the product decision above reasoning was never meant to be kept anyway.

**No docker-compose or config changes.** No new env vars, no new ports —
same as M5. `StreamingResponse`'s existing CORS middleware (`app/main.py`,
already configured for `allow_credentials=True` + explicit
`frontend_origin` since the household-grounding fast-follow) applies to
streamed responses with no changes needed.

---

## File-by-file plan

### `services/ai-service/app/adapters/base.py`
- Add a `StreamChunkKind = Literal["thinking", "answer"]` alias.
- Add `StreamFilter` protocol/ABC: `feed(self, delta: str) ->
  list[tuple[StreamChunkKind, str]]`, `flush(self) -> list[tuple[StreamChunkKind, str]]`.
- Add abstract `create_stream_filter(self) -> StreamFilter` to
  `ModelAdapter`.

### `services/ai-service/app/adapters/qwen.py`
- New `_QwenStreamFilter` implementing the three-state (`detecting_open` /
  `thinking` / `answering`) machine described above, using the existing
  `_THINK_BLOCK` tag literals (`<think>`, `</think>`) rather than the
  compiled whole-string regex (which needs a complete string, not a delta
  stream).
- `QwenAdapter.create_stream_filter()` returns a fresh `_QwenStreamFilter()`
  instance (one per chat request — filters are stateful and must not be
  shared across concurrent requests, so `get_adapter()`'s existing
  process-wide singleton returns the same *adapter*, but each call to
  `create_stream_filter()` returns a brand new filter instance).

### `services/ai-service/app/ollama.py`
- Add `async def chat_stream(messages: list[dict]) -> AsyncIterator[dict]:`
  — opens `client.stream("POST", "/api/chat", json={"model":
  settings.ollama_model, "messages": messages, "stream": True})`, iterates
  `resp.aiter_lines()`, `json.loads`s each non-empty line, yields the dict.
  Raises `resp.raise_for_status()` before iterating (same as `chat()`) so a
  bad initial connection surfaces the same way it does today.

### `services/ai-service/app/routers/chat.py`
- `build_system_prompt()`, retrieval/citation/entity-grounding gathering:
  **unchanged**, same call shape as today.
- Replace the single `await ollama.chat(...)` + `ChatResponse` return with
  an inner `async def _event_stream() -> AsyncIterator[bytes]:` generator:
  - Yield the `citations` frame first: `event: citations\ndata:
    {json list of citation_list, via Citation.model_dump_json() per item}\n\n`.
  - Get a fresh filter via `get_adapter().create_stream_filter()`.
  - `async for chunk in ollama.chat_stream(messages):` — feed
    `chunk["message"]["content"]` through the filter; for each returned
    `(kind, text)` segment, yield `event: thinking\ndata:
    {"content": "<text>"}\n\n` or `event: token\ndata: {"content":
    "<text>"}\n\n` depending on `kind` (JSON-encode so embedded
    newlines/quotes in model output can't corrupt SSE framing).
  - After the loop (natural `done: true` termination), call `filter.flush()`
    and yield any frames it returns (the unterminated-think-block edge
    case — per the adapter's own fallback, `flush()` classifies leftover
    buffer as `"answer"`, so this always surfaces as a `token` frame, never
    a `thinking` one).
  - Wrap the whole body in `try/except httpx.HTTPError`: on catch, yield
    `event: error\ndata: {"detail": "ai-service could not reach the local
    model"}\n\n` and return — mirrors the existing 502 message text, just
    delivered as an SSE frame instead of an HTTP status.
  - `return StreamingResponse(_event_stream(), media_type="text/event-stream")`.
- `ChatResponse`/`Citation` imports: `ChatResponse` schema class itself can
  go — nothing constructs one anymore. `Citation` is still needed for the
  `citations` frame's JSON shape.

### `services/ai-service/app/schemas/chat.py`
- `ChatResponse` is no longer used by `routers/chat.py` — leave the class
  in place only if something else imports it (check `test_chat.py`); if
  nothing does, remove it rather than leaving dead schema behind. `Citation`
  stays, unchanged.

### `services/ai-service/tests/test_adapters.py`
- New cases for `QwenAdapter().create_stream_filter()`:
  - Feed deltas that assemble `<think>reasoning</think>hello` split across
    3+ separate `feed()` calls at arbitrary boundaries (mid-tag splits
    included) → concatenating every `("thinking", text)` segment across all
    `feed()` calls equals `"reasoning"`, and every `("answer", text)`
    segment equals `"hello"`, in that relative order (thinking fully
    finishes before any answer segment appears).
  - A single delta containing the entire `</think>hello` boundary in one
    piece → that one `feed()` call returns *two* segments in order:
    `("thinking", ...)` then `("answer", "hello")`.
  - Feed a delta that doesn't start with `<` at all → returned immediately
    on the very first `feed()` call as `("answer", ...)`, unbuffered (the
    "model didn't think" / divergence-detected path) — never emitted as
    `"thinking"`.
  - Feed `<think>cut off, no closing tag` (nothing in it is ambiguous with
    `</think>`) → the whole thing streams out as `("thinking", ...)`
    immediately from `feed()` itself; `flush()` afterward is `[]` since
    nothing was left buffered.
  - Feed `<think>partial reasoning cut off with <` (ends in a lone `<`,
    an ambiguous prefix of `</think>`) → `feed()` returns the safe prefix
    as `"thinking"`, holding back just the trailing `<`; `flush()` then
    returns that held-back `<` classified as `"thinking"` too (still
    reasoning text, just unconfirmed).
  - Feed `<thi` (never even confirms the reply opened with `<think>` at
    all) → `feed()` returns `[]`; `flush()` returns
    `[("answer", "<thi")]` — the one case that *does* reclassify as
    `"answer"`, since nothing was ever confirmed to be reasoning.
  - Feed nothing, call `flush()` immediately → `[]`.
  - Two independent `create_stream_filter()` calls never share state.

### `services/ai-service/tests/test_ollama.py` (new)
- Following the existing `httpx.MockTransport`/monkeypatch conventions used
  elsewhere in this test suite: `chat_stream()` against a fake NDJSON
  response body (multiple `{"message": {"content": "..."}, "done": false}`
  lines + a final `"done": true` line) yields the parsed dicts in order;
  a blank line in the body is skipped rather than raising; an HTTP error
  status raises `httpx.HTTPStatusError` before any line is yielded.

### `services/ai-service/tests/test_chat.py`
- Existing tests currently assert on `resp.json()` against the old
  single-JSON-body contract — rewritten to instead: monkeypatch
  `ollama_module.chat_stream` (async generator fake) instead of
  `ollama_module.chat`, `client.post("/chat", ...)` as before (`TestClient`
  fully drains a `StreamingResponse` synchronously, so `resp.text` holds
  the complete SSE payload), then assert on the parsed sequence of
  `event:`/`data:` frames (small local helper to split `resp.text` into
  `(event, data)` tuples) rather than a single JSON body — a `citations`
  frame first (possibly empty-list), then zero or more `thinking` frames,
  then one or more `token` frames whose concatenated `content` equals the
  previously-asserted full message text, covering: no context
  (`NO_CONTEXT_SYSTEM_PROMPT` unaffected), injected retrieved chunks,
  citations present, and a `<think>...</think>`-prefixed reply producing
  `thinking` frames that all precede the `token` frames (never
  interleaved with them).
- New case: `ollama_module.chat_stream` raising `httpx.HTTPError` partway
  through yielding → response is still `200`, an `error` frame appears in
  the stream, and no further `thinking`/`token` frames follow it.
- New case: a fake `chat_stream` whose only content is an unterminated
  `<think>` block with no close → the buffered reasoning surfaces as a
  `token` frame (not `thinking`) via `flush()`'s fallback classification,
  matching the adapter's own fallback semantics feeding through end-to-end.

### `services/frontend/src/api/chat.ts`
- `ChatMessage`, `ChatCitation`: unchanged.
- Remove `sendChatMessage`'s single-JSON-response implementation. Add
  `streamChatMessage(messages: ChatMessage[], handlers: { onCitations:
  (c: ChatCitation[]) => void; onThinking: (delta: string) => void;
  onToken: (delta: string) => void }, signal: AbortSignal): Promise<void>`
  — `fetch` with the same body/headers/`credentials: 'include'` as before,
  `response.body!.getReader()`, decode + buffer + split on `"\n\n"`, parse
  each frame's `event`/`data` lines, dispatch to the matching handler
  (`thinking`/`token`/`citations`); on an `error` frame or a non-`res.ok`
  initial response, throw `AiServiceError` (reusing the existing
  class/`parseErrorDetail` for the non-ok case; a same-shape error
  constructed directly from the frame's `detail` for the in-stream case) so
  `ChatPage`'s existing error-banner code path needs no branching on *why*
  it failed.

### `services/frontend/src/hooks/useSendChatMessage.ts` → `useStreamChatMessage.ts`
- Rename/rewrite: `useState<boolean>` for `isPending`, `useState<Error |
  null>` for `error`, `useRef<AbortController | null>`. `send(messages,
  { onCitations, onThinking, onToken })`: aborts any in-flight previous
  call, creates a fresh controller, sets `isPending = true`/`error = null`,
  calls `api.streamChatMessage(...)`, resolves/rejects update
  `isPending`/`error` same as a `useMutation` would. Returned shape
  (`{ send, isPending, error }`) intentionally mirrors enough of
  `useMutation`'s surface that `ChatPage.tsx`'s existing
  `sendMessage.isPending`/`.isError`/`.error` reads need minimal renaming.

### `services/frontend/src/pages/ChatPage.tsx`
- New local state: `thinkingPreview: string` (the live reasoning text,
  reset to `""` at the start of every send) — this is the "Thinking…"
  slot's replacement, not part of `messages`, never resent, never kept.
- `send()`: instead of `.mutate(nextMessages, {onSuccess})`, calls
  `sendMessage.send(nextMessages, { onCitations, onThinking, onToken })`:
  - `onCitations`: stash the citation list in a ref (the placeholder
    message doesn't exist yet — citations always arrive first).
  - `onThinking`: append `delta` to `thinkingPreview`.
  - `onToken`: on the *first* call this turn, clear `thinkingPreview` back
    to `""`, append a new `{role: 'assistant', content: '', citations:
    <the stashed ref value>}` to `messages`, and record its index; every
    call (including this first one) appends `delta` to that message's
    `content` via index-matched functional `setMessages`.
- On `error` (surfaced via the hook's `error` state changing, same as
  today's `isError` check): clear `thinkingPreview`, and if the real
  message placeholder was already created this turn, remove it from
  `messages` (splice by the recorded index) before showing the existing
  error banner — see the design decision above on why this must happen for
  `retry()` to stay correct.
- `retry()`: unchanged — still resends `toWireMessages(messages)`.
- Render: where the old static "Thinking…" `<div>` sat, show it only when
  `sendMessage.isPending && !thinkingPreview` (nothing has arrived yet);
  once `thinkingPreview` is non-empty, render it instead in a visually
  distinct (dimmed/italic) block so it's unambiguous this is in-progress
  reasoning, not the final answer; once the real placeholder message
  exists in `messages`, it renders through the normal `ChatBubble` map
  exactly like any other message, and `thinkingPreview` is empty again so
  the temporary block is already gone.

### `services/frontend/src/components/ChatBubble.tsx`
- No change needed. It renders `message.content` via `ReactMarkdown` and
  `citations` underneath — an incrementally-growing `content` string
  re-renders correctly with no special-casing, and it never sees
  reasoning text at all (that lives in `ChatPage`'s separate
  `thinkingPreview` block, not in any `DisplayMessage`). A
  streaming-cursor affordance on the real answer bubble was considered and
  deliberately dropped — not needed for the exit criterion, and
  `ReactMarkdown` re-parsing a partial markdown string every token is
  already the interesting risk to watch for in manual testing, not worth
  compounding with extra visual polish this milestone doesn't ask for.

---

## Sequencing

1. `ai-service`: `adapters/base.py` (`StreamFilter` protocol),
   `adapters/qwen.py` (`_QwenStreamFilter`) + extended `test_adapters.py` —
   land and verify this piece in isolation first, since it's the trickiest
   pure logic in the milestone and has zero dependency on anything else
   changing.
2. `ai-service`: `ollama.py::chat_stream()` + new `test_ollama.py`.
3. `ai-service`: `routers/chat.py` SSE conversion + rewritten `test_chat.py`.
   Verify against the running `docker-compose` stack with `curl -N` (or
   `httpx` in stream mode) against `/chat` directly — confirm the raw
   response body is well-formed `event:`/`data:` frames, a `citations`
   frame arrives first, and think-block content never appears in a `token`
   frame, before touching the frontend at all.
4. `frontend`: `api/chat.ts` (`streamChatMessage`), `useStreamChatMessage.ts`,
   `ChatPage.tsx` wiring. Confirm `npm run lint`/`build` clean.
5. Manual browser walkthrough end-to-end (below).

---

## Verification

**Manual walkthrough:** log in, open Chat, ask a question that hits both
grounding paths (a question about an uploaded document *and* a tracked
household record, same as M5's "talking points for my next call with Dad"
test data if it's still seeded). Confirm: the static "Thinking…" indicator
shows only briefly (or not at all), replaced by ARIA's live reasoning text
growing in the dimmed preview block as `thinking` frames arrive; the moment
the model moves past its think block, that preview disappears and the real
assistant bubble appears and visibly grows token-by-token (not one
instantaneous paste); the "Sources" pill row is present on that bubble from
the moment it appears (carried over from the `citations` frame, resolved
before either kind of content streamed); and the final rendered content
matches what M5's non-streaming path would have produced for the same
question, with no reasoning text anywhere in the permanent message list.
Ask a follow-up in the same conversation and confirm the resent request
still succeeds (same 422-avoidance check M5's plan called out — inspect the
outgoing request body in devtools to confirm resent history messages carry
only `{role, content}`, no partial-streaming or reasoning artifacts).

**Error-path walkthrough:** stop `ollama` mid-conversation (after the
request has already started streaming, if timing allows, to specifically
exercise the mid-stream `error` frame rather than just a connection
failure) and confirm the existing error banner + Retry button appear with
no half-written assistant bubble left behind in the message list; click
Retry after restarting `ollama` and confirm it succeeds.

Then the unchanged strict-decoupling checks from M4/M5 (retrieval/entity
grounding/citations are untouched by this milestone, but worth a quick
re-confirmation given the router rewrite): stop `chromadb` → ungrounded
streamed answer, no `token` frame delay introduced; stop `core-api` →
grounded answer still streams, `citations` frame is empty.

**Automated:** `uv run pytest` in `ai-service` (extended `test_adapters.py`,
new `test_ollama.py`, rewritten `test_chat.py`); `npm run lint` + `npm run
build` in `frontend`. No frontend unit test runner exists in this project
(`package.json` has no `test` script) — same as M3/M5, the streaming
`fetch`/`ReadableStream` parsing logic in `api/chat.ts` and the placeholder
lifecycle in `ChatPage.tsx` are verified by the manual walkthrough only,
not by an automated frontend test.

### Critical files
- `services/ai-service/app/adapters/base.py`
- `services/ai-service/app/adapters/qwen.py`
- `services/ai-service/app/ollama.py`
- `services/ai-service/app/routers/chat.py`
- `services/frontend/src/api/chat.ts`
- `services/frontend/src/hooks/useStreamChatMessage.ts` (renamed from
  `useSendChatMessage.ts`)
- `services/frontend/src/pages/ChatPage.tsx`
