# sd12: Auth + rate limiting on `POST /chat`

Closes `scaling-debt.md` item [#11](../scaling-debt.md#11-post-chat-requires-no-authentication-and-has-no-rate-limiting).

## What shipped

`services/ai-service/app/core_api_client.py` gained a strict authorization
gate, `require_chat_session(cookie: str | None) -> SessionContext`, that
replaces the old `get_current_household_id` (deleted — its one real caller
is the function this replaces, and this repo's convention is not to keep
unused soft-degrade primitives around "just in case"). Unlike the
degrade-to-`None` helpers everywhere else in this module,
`require_chat_session` either returns a real, fully-populated
`aria_auth.SessionContext` (`household_id`, `user_id`, `user_name`, `role`)
or raises:

- No cookie at all, or `GET /auth/me` returns 401 → `ChatUnauthenticated`
  (`chat.py` maps this to `401`).
- Any other failure calling `/auth/me` — timeout, connection error, 5xx, an
  unparseable body — → `ChatAuthUnavailable` (`chat.py` maps this to `503`).
  This is a **deliberate deviation from `sd11`'s Redis fail-open
  precedent**, confirmed with the user before implementation: `/auth/me` is
  the *primary* authorization check for `/chat`, not a secondary system
  sitting in front of one, so its own failure can't be treated as "not
  authenticated, proceed anyway." Every other `core_api_client.py` helper's
  fail-open stance is untouched — this asymmetry is scoped to the one
  function that's actually gating access.

`routers/chat.py`'s `chat()` calls `require_chat_session(session_cookie)`
once, at the very top, before branching on `request.resume` — so both the
fresh-message path and the resume (confirm/cancel a proposed write action)
path are gated identically, and neither ever reaches the LangGraph/Ollama
pipeline on a rejected request. `household_id` is now always a real `str`
by the time it reaches `_route_and_gather`/`_resume_action` — downstream
`str | None` handling in `retrieval.py`/`entity_grounding.py`/`agents/nodes.py`
was left untouched as defensive code that's simply no longer exercised in
practice, not worth ripping out for this item.

Two new modules, mirroring core-api's `sd11` shape as closely as possible:

- `app/redis_client.py` — a lazy `redis.asyncio.Redis` singleton (same
  `_client`/`get_client()` global pattern as core-api's), pointed at a new
  `chat_rate_limit_redis_url` setting. Reuses the `agent-store` Redis Stack
  container `ai-service` already depends on for its LangGraph checkpointer
  — same container, a different logical db index (checkpointer=0, chat rate
  limit=1) — no new infra, same "same container, different db" pattern
  core-api used reusing its Celery-broker Redis for `sd11`.
- `app/rate_limit.py` — `check_chat_rate_limit(user_id: str)`, a single
  fixed-window `INCR`+`EXPIRE` counter keyed `ratelimit:chat:user:{user_id}`,
  checked and incremented together on every call regardless of outcome
  (mirrors core-api's signup counter shape, not login's split — this is
  about request *volume*, not credential guessing). Keyed by `user_id`, not
  IP, since every caller is authenticated by this point — a household
  sharing one IP must not throttle every member off one member's usage.
  Fails open on any `redis.exceptions.RedisError` (logged as a warning) —
  this fail-open stance is *not* revisited by the auth gate's fail-closed
  decision above; the rate limiter is a genuinely secondary system, same
  reasoning `sd11` already established.

`chat()` calls `check_chat_rate_limit(session.user_id)` right after auth
succeeds, before any graph/Ollama work. 429 responses carry a generic
`detail` and a `Retry-After` header from the counter's actual remaining TTL.

New settings in `config.py`: `chat_rate_limit_redis_url` (default
`redis://agent-store:6379/1`), `chat_rate_limit_requests` (default `20`),
`chat_rate_limit_window_seconds` (default `300`) — reasoned from chat's own
bursty-within-one-conversation shape (a propose-then-resume write action
costs 2 requests, not 1) rather than copied from login's isolated-attempt
10/15min. Not exposed via compose — tunable later via env, same treatment
`sd11` gave its four login/signup threshold settings. `docker-compose.yml`/
`docker-compose.prod.yml` gained `AI_SERVICE_CHAT_RATE_LIMIT_REDIS_URL`
alongside the existing `AI_SERVICE_REDIS_URL` line.
`services/ai-service/pyproject.toml` gained an explicit `redis>=5.0`
dependency (previously only pulled in transitively via
`langgraph-checkpoint-redis`).

## Explicitly out of scope (confirmed with the user before implementation)

No concurrency/semaphore limiter on `/chat`, no changes to Ollama call
concurrency — that's `scaling-debt.md` item #14 ("single-GPU Ollama, no
concurrency limit or queue"), already tracked separately as its own,
heavier "blocks-AWS-launch" item. The per-user rate limiter here is a real
but **partial** mitigation for GPU contention: it stops a scripted flood or
a runaway retry-loop bug from one identity, it does not serialize
concurrently-arriving legitimate users onto the single GPU — #14 is where
real queueing/backpressure belongs.

## Tests

`tests/conftest.py` gained two autouse fixtures mirroring `sd11`'s test
setup: `fake_chat_auth` (monkeypatches `require_chat_session` to return a
fixed fake session by default, so the ~25 pre-existing `test_chat.py` calls
— most of which never set a cookie, since they exist to exercise
routing/retrieval/citation behavior, not auth — keep passing unmodified)
and `fake_redis` (a hand-rolled in-memory fake — `get`/`incr`/`expire`/`ttl`
— copied from core-api's `tests/conftest.py` `_FakeRedis`/`_BrokenRedis`,
function-scoped so no counter state leaks between tests).

`test_chat.py`: one test retargeted its mock from `get_current_household_id`
to `require_chat_session`; one test (asserting a cookie-less request
proceeds ungrounded) was deleted — its premise is exactly the hole this
closes, and its intent moved to the new 401-no-cookie test.

New `test_chat_auth.py`: 401-no-cookie (asserts the agent graph is never
built), 401-invalid-session, 503-core-api-unreachable, resume-path also
gated, 429-after-the-real-default-threshold, and
rate-limit-fails-open-when-redis-down.

`test_core_api_client.py`: swapped the three `get_current_household_id`
tests for equivalent `require_chat_session` ones (success, 401,
unreachable, malformed-response-shape), reusing the file's existing
`FakeAsyncClient`/`FakeResponse` doubles.

227 `ai-service` tests pass.

## Verified live against the real running dev stack

- `POST /chat` with no cookie → `401`, `{"detail":"not authenticated"}`.
- `POST /chat` with a garbage cookie → `401`, same detail.
- Stopped `core-api` (`docker compose stop core-api`), hit `/chat` with a
  valid-looking cookie → `503`, `{"detail":"authentication service
  unavailable"}` — fails closed as designed, not a hang or a 200 degrade.
  Restarted `core-api`, confirmed healthy again.
- Logged in for real (`owner@household.local`); confirmed via `core-api`'s
  own access log (`GET /auth/me 200 OK`) that a valid session passes the
  gate; confirmed the rate-limit counter key
  (`ratelimit:chat:user:<real-user-id>`) landed on `agent-store` db 1 after
  an authenticated request, while the LangGraph checkpointer's ~87 keys
  stayed on db 0 — the two systems sharing one container without colliding,
  as designed.
- Primed the rate-limit counter to the configured threshold directly via
  `redis-cli` (to avoid needing 20 real end-to-end chat completions in this
  environment — see note below) and confirmed the next authenticated
  request returned `429 Too Many Requests` with a `Retry-After: 297` header
  and the expected generic detail message.
- Stopped `agent-store` mid-session and sent an authenticated request:
  confirmed via logs that both the rate-limit check *and* increment logged
  their fail-open warnings (`chat rate limit check failed, failing open...`
  / `chat rate limit increment failed, not counted...`) rather than
  blocking or 500ing — the pre-existing LangGraph checkpointer degrade path
  (unrelated to this change) also kicked in as already designed. Restarted
  `agent-store`, cleaned up the manually-primed test key.
- Full `ai-service` test suite (227 tests) passes inside the dev container.

**Not independently re-verified this session:** full token-by-token SSE
streaming for a valid, authenticated request. In this sandbox, real Ollama
inference against the loaded `qwen3:14b` model did not return within
several minutes for a trivial prompt — this reproduces the exact,
already-documented characteristic of `scaling-debt.md` #14 ("no queue, no
429, no shorter timeout to fail fast — a backed-up Ollama just makes every
concurrent user's request hang for up to 5 minutes with no backpressure
signal"), not a regression introduced here. The auth gate and rate limiter
both run and resolve *before* any Ollama call, and were directly confirmed
to pass a valid session through without a 401/429 (via core-api's own
`/auth/me 200` log line and the resulting rate-limit counter increment)
before the request reached that pre-existing bottleneck. The frontend's
generic error-surfacing for a non-2xx `/chat` response (`api/chat.ts`'s
`streamChatMessage` → `AiServiceError` → `useStreamChatMessage`'s `error`
state → `ChatPage.tsx` rendering `error.message`) was re-read but not
re-exercised live in a browser this session — same conclusion `sd11`
already proved for `/auth/login`/`/auth/signup` against this exact
mechanism, unmodified here.
