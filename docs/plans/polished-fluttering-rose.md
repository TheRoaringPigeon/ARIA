# sd12: Auth + rate limiting on `POST /chat`

## Context

`scaling-debt.md` item [#11](../scaling-debt.md#11-post-chat-requires-no-authentication-and-has-no-rate-limiting)
is the next unaddressed item in sequence (item #10, the `/auth/login`/
`/auth/signup` rate limiter, shipped as `sd11` and explicitly scoped `/chat`
out as future work). Today, `ai-service`'s `POST /chat` accepts a request
with no session cookie at all, or an expired/invalid one, and still runs the
full LLM pipeline (2+ real Ollama calls) in a silently degraded,
effectively-unauthenticated mode — nothing rejects it, and nothing limits
how many times it can be called. This is a real open-internet abuse surface
(anyone can burn GPU time with zero login) and the most direct way one bad
actor or one buggy retry loop starves every other household's chat once this
is reachable outside a home LAN. This plan closes both halves of #11:
requiring real authentication, and adding a per-user request-rate limiter.
It deliberately does **not** attempt to fix GPU concurrency contention itself
— that is scaling-debt item #14, already tracked separately as its own
heavier "blocks-AWS-launch" item (queueing/backpressure in front of Ollama).

Two decisions were confirmed with the user before this plan was finalized:
1. When core-api itself is unreachable during the auth check (as opposed to
   a cookie simply being missing/invalid), `/chat` **fails closed** (503) —
   a deliberate deviation from `sd11`'s Redis fail-open precedent, because
   `/auth/me` is the *primary* authorization check for this endpoint, not a
   secondary system sitting in front of one.
2. This item stays scoped to auth + a per-user request-rate limiter. No
   concurrency/semaphore cap is added here; that belongs to #14.

## Design

### 1. Strict auth gate — `services/ai-service/app/core_api_client.py`

Add an exception pair and a new function, `require_chat_session`, that
either returns a real `aria_auth.SessionContext` (`household_id`, `user_id`,
`user_name`, `role` — already the exact shape `/auth/me` returns) or raises:

- No cookie at all, or `/auth/me` returns 401 → `ChatUnauthenticated` (maps
  to HTTP 401 — "not authenticated").
- Any other failure calling `/auth/me` (timeout, connection error, 5xx, a
  malformed body) → `ChatAuthUnavailable` (maps to HTTP 503 — "authentication
  service unavailable"). core-api's own health is unverifiable in this
  branch, so `/chat` fails closed instead of degrading to
  ungrounded-but-served.

Delete `get_current_household_id` (its one real caller, `chat.py`, is being
replaced; grep confirms no other call sites — two docstrings that name it in
passing, in `get_household` and `retrieval.py`, get a one-line reword, no
signature/behavior change). This matches the repo's convention of not
keeping unused soft-degrade primitives around "just in case."

### 2. `services/ai-service/app/routers/chat.py`

`chat()` calls `require_chat_session(session_cookie)` once, at the very top,
before branching on `request.resume` — so both the fresh-message path
(`_route_and_gather`) and the resume path (`_resume_action`) are gated
identically, and neither ever runs on a 401/503. `ChatUnauthenticated`/
`ChatAuthUnavailable` map to `HTTPException(401, ...)`/`HTTPException(503,
...)`. `household_id` becomes `session.household_id` — always a real `str`
from here on. Downstream signatures (`_route_and_gather`, `_resume_action`,
`agents.gather_baseline_context`, `retrieval.py`, `entity_grounding.py`) keep
their existing `str | None` handling untouched — defensive code that's
simply never hit in production anymore, not worth ripping out for this item.

### 3. Rate limiter — new `redis_client.py` + `rate_limit.py` in `ai-service`

Mirrors core-api's `sd11` modules as closely as possible (same `_client`/
`get_client()` global-singleton shape, same `_retry_after_if_limited`/
`_record_attempt`/`_raise_429` structure, same fail-open-on-`RedisError`
stance — that fail-open stance is unrelated to and not revisited by the
fail-closed auth decision above, since the rate limiter is a genuinely
secondary system):

- `check_chat_rate_limit(user_id: str)` — one fixed-window `INCR`+`EXPIRE`
  counter keyed `ratelimit:chat:user:{user_id}`, checked then incremented
  together on every call regardless of outcome (mirrors the signup counter's
  shape, not login's split — this is about request *volume*, not credential
  guessing). Keyed by `user_id`, not IP, since every caller is authenticated
  by this point — a household sharing one IP must not throttle every member
  off one member's usage.
- Called in `chat()` right after `require_chat_session` succeeds, before any
  graph/Ollama work, for both the fresh-message and resume paths.
- Infra: reuses the `agent-store` Redis Stack container `ai-service` already
  depends on for its LangGraph checkpointer, on a **different db index** (db
  1; the checkpointer uses db 0) — same "same container, different logical
  db" pattern core-api used reusing its Celery-broker Redis for `sd11`. No
  new container, no new compose service.
- New settings in `config.py`: `chat_rate_limit_redis_url` (default
  `redis://agent-store:6379/1`), `chat_rate_limit_requests` (default `20`),
  `chat_rate_limit_window_seconds` (default `300`) — reasoned from chat's
  bursty-within-one-conversation shape (unlike login's isolated-attempt
  shape), not copied verbatim from login's 10/15min. Wired into
  `docker-compose.yml`/`docker-compose.prod.yml` alongside the existing
  `AI_SERVICE_REDIS_URL` line. `pyproject.toml` gains an explicit
  `redis>=5.0` dependency (today only pulled in transitively via
  `langgraph-checkpoint-redis`).

### 4. Tests

`tests/conftest.py` gains two autouse fixtures, mirroring `sd11`'s test
setup exactly:
- `fake_chat_auth` — monkeypatches `require_chat_session` to return a fixed
  fake `SessionContext` by default, so the ~25 existing `test_chat.py` calls
  (most of which never set a cookie today, since they're exercising routing/
  retrieval/citation behavior, not auth) keep passing unmodified.
- `fake_redis` — a hand-rolled in-memory fake (`get`/`incr`/`expire`/`ttl`,
  copied from core-api's `tests/conftest.py` `_FakeRedis`/`_BrokenRedis`, no
  `fakeredis` dependency), function-scoped so no counter state leaks between
  tests.

`test_chat.py`: one existing test (`test_chat_resolves_household_id_and_threads_it_to_retrieval`)
retargets its mock from `get_current_household_id` to `require_chat_session`.
One existing test (`test_chat_skips_household_id_lookup_without_a_cookie`) is
deleted — its premise (no cookie, request still proceeds ungrounded) is
exactly the hole this closes; its *intent* moves to the new 401-no-cookie
test below.

New `test_chat_auth.py`: 401-no-cookie (and asserts the graph is never
invoked), 401-invalid-session, 503-core-api-unreachable, resume-path also
gated, 429-after-threshold (using the real default threshold), and
rate-limit-fails-open-when-redis-down.

`test_core_api_client.py`: swap the three `get_current_household_id` tests
for equivalent `require_chat_session` ones (success, 401, unreachable,
malformed-response-shape), reusing the file's existing `FakeAsyncClient`/
`FakeResponse` doubles (already support an injectable `status_code`).

### 5. Frontend

No code changes expected — `api/chat.ts`'s `streamChatMessage` checks
`res.ok` before ever touching the SSE body, and any non-2xx already becomes
an `AiServiceError(status, detail)` that `useStreamChatMessage`/`ChatPage.tsx`
already render generically (same conclusion `sd11` reached for
`/auth/login`/`/auth/signup`). Verified live in a browser below, not just
assumed.

## Verification (live, against the real running dev stack)

- `POST /chat` with no cookie → `401`, `{"detail":"not authenticated"}`.
- A stale/garbage cookie → `401`.
- Stop `core-api`, hit `/chat` with a valid-looking cookie → `503`, not a
  hang or a 200 degrade.
- Log in for real, confirm `/chat` still streams normally end-to-end.
- Script `chat_rate_limit_requests + 1` rapid authenticated calls → the last
  one is `429` with a sane `Retry-After`.
- Stop `agent-store` mid-session, confirm `/chat` still succeeds (rate
  limiter fails open) rather than 500ing.
- `docker exec` into `agent-store`, confirm rate-limit keys land on db 1
  (`redis-cli -n 1 KEYS 'ratelimit:*'`) while checkpoints stay on db 0.
- Browser check: log out, confirm the chat composer surfaces the 401 detail
  message instead of hanging or throwing uncaught.
- Full `ai-service` test suite passes.

## Write-up

Once implemented and verified, record what shipped in
`docs/scaling-debt-plans/sd12-chat-auth-rate-limiting.md` (same structure as
`sd11`'s doc: what shipped, tests, verified-live, scope decisions — including
the fail-closed-vs-fail-open asymmetry and the explicit #14 scope boundary),
and update `scaling-debt.md` item #11's status marker and add the closing
note pointing at it, matching every other fixed item's convention in that
file.
