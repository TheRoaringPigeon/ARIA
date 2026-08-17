# sd11: Rate limiting on `/auth/login` and `/auth/signup`

Closes `scaling-debt.md` item [#10](../scaling-debt.md#10-no-rate-limiting-or-brute-force-protection-on-authlogin-or-authsignup).

## What shipped

Two new modules in `services/core-api/app/`:

- `redis_client.py` — a lazy `redis.asyncio.Redis` singleton, mirroring
  `app/db.py`'s `_client`/`get_client()` pattern exactly, pointed at a new
  `rate_limit_redis_url` setting (`redis://redis:6379/2` — same physical
  Redis container as the Celery broker/result backend, just a distinct
  logical db index for hygiene, not correctness). Short `socket_connect_timeout`/
  `socket_timeout` (2s each), same rationale `app/celery_client.py` already
  applies to its broker connection: this sits in front of every login/signup
  request, so a down Redis must fail fast.
- `rate_limit.py` — plain functions (no `Depends()` abstraction; only 2 call
  sites, and login/signup have genuinely different increment semantics, so a
  one-size-fits-all checker would need a flag to paper over that). Fixed-window
  counters via `INCR`+`EXPIRE`:
  - `check_login_rate_limit(request, email)` / `record_login_failure(request, email)`
    — two independent counters, per caller IP and per target email
    (lowercased), incremented **only on a failed login** — never on success,
    never on request volume alone, so a household sharing one IP or a user
    who mistypes a password once then succeeds is never throttled. 10
    attempts / 15 minutes, each axis independent.
  - `check_and_record_signup_attempt(request)` — one per-IP counter,
    incremented on **every** attempt regardless of outcome (a duplicate-email
    409 still consumes quota) — the abuse this guards against is
    household-creation volume, not credential guessing. 5 attempts / hour.
  - Both fail open on any `redis.exceptions.RedisError`: logged as a warning,
    treated as "not limited" — a deliberate choice, confirmed with the user,
    matching this codebase's existing stance that a secondary system's
    outage shouldn't block a primary write path (e.g. Celery enqueue
    failures don't block CRUD writes elsewhere).
  - 429 responses carry a generic `detail` (no remaining-attempts/threshold
    numbers leaked) and a `Retry-After` header set from the counter's actual
    remaining TTL.
  - `get_client_ip()` prefers the first `X-Forwarded-For` entry, falling back
    to `request.client.host`. Safe to trust unconditionally in this app's
    topology: in prod (`docker-compose.prod.yml` + `Caddyfile`), core-api has
    no published port and is reachable *only* via Caddy's `reverse_proxy`,
    which sets `X-Forwarded-For` on every request it proxies — nothing else
    can reach core-api directly to spoof it. In dev, core-api's port is
    published with no proxy in front at all, so there's no header to spoof
    either; the fallback is already the real caller.

`routers/auth.py`: both routes gained a `request: Request` param.
`check_login_rate_limit`/`check_and_record_signup_attempt` run first, before
any Mongo work; `record_login_failure` runs in the existing 401 branch. No
other route logic changed.

`config.py` gained `rate_limit_redis_url` and the four threshold/window
settings (`login_rate_limit_attempts`, `login_rate_limit_window_seconds`,
`signup_rate_limit_attempts`, `signup_rate_limit_window_seconds`), all
overridable via env, no compose entries needed since the defaults suit both
dev and prod (same category as `max_page_limit`). `docker-compose.yml`/
`docker-compose.prod.yml` gained explicit `CORE_API_RATE_LIMIT_REDIS_URL`
wiring, matching the existing `CORE_API_CELERY_BROKER_URL` precedent.
`pyproject.toml` gained an explicit `redis>=5.0` dependency — previously only
pulled in transitively via `celery[redis]`; the code now imports
`redis.asyncio`/`redis.exceptions` directly.

## Frontend

No changes needed. `src/api/client.ts`'s `ApiError`/`parseErrorDetail`
already generically surfaces any non-2xx response's `detail` field, and
`LoginPage.tsx` already renders it — a 429 displays correctly with zero
frontend code changes.

## Tests

`tests/conftest.py` gained an autouse `fake_redis` fixture: a hand-rolled
in-memory fake (`get`/`incr`/`expire`/`ttl`, monotonic-clock expiry) — no
`fakeredis` dependency, matching this repo's existing preference for small
hand-rolled fakes (`_RecordingCelery`). Function-scoped, so no counter state
leaks between tests; every pre-existing test (which calls login/signup at
most 1-2 times) stays well under the new default thresholds unmodified. A
second fake, `_BrokenRedis` (every method raises
`redis.exceptions.ConnectionError`), is used only inside the two fail-open
tests via a one-off `monkeypatch.setattr` override.

New `tests/test_auth_rate_limit.py` (8 cases): login blocked at the
configured attempt count from one IP; a single failed login stays 401 (not
over-triggering); login blocked by the **email** counter even across
different `X-Forwarded-For` IPs; repeated *successful* logins past the
threshold never trigger 429 (success never increments); signup blocked at
its configured count from one IP; a duplicate-email 409 still consumes
signup quota; login and signup both fail open (still resolve normally, not
500) when Redis is unreachable.

259 `core-api` tests pass (258 + the pre-existing `test_seed_admin_defaults_off`
failure, confirmed via `git stash` to predate this change entirely — it's an
artifact of running `pytest` inside the dev container, which sets
`CORE_API_SEED_ADMIN=true`, conflicting with a test that instantiates a bare
`Settings()` and asserts the *default* is `False`; unrelated to rate
limiting).

## Verified live against the real running dev stack

- Flushed the rate-limit Redis db (`redis-cli -n 2 FLUSHDB`) for a clean
  baseline, then scripted 12 rapid wrong-password `POST /auth/login` calls
  against a fresh email: attempts 1–10 returned `401`, attempt 11 returned
  `429` with `Retry-After: 898`, attempt 12 also `429` — exactly matching the
  configured `login_rate_limit_attempts=10`.
- Confirmed the real seeded user (`owner@household.local` / `aria-dev`)
  still logs in with `200` after flushing counters.
- Stopped the `redis` container (`docker compose stop redis`) mid-session:
  a wrong-password login still returned `401` (not 500/429), a
  correct-password login still returned `200`, and `core-api`'s logs showed
  the expected caught `redis.exceptions.TimeoutError` logged as a warning
  with traceback, not an unhandled exception. A signup with a fresh email
  also still succeeded (`201`) while Redis was down. Restarted `redis`
  afterward (`docker compose start redis`, confirmed `PONG`).

## Scope decision

Thresholds (10 login attempts/15min per IP and independently per email; 5
signups/hour per IP) are configured but not exposed via compose — an
operator can tune any of the four settings later via env without a compose
change, same treatment as `max_page_limit`. No global/unauthenticated rate
limiter was added anywhere else in the app (e.g. `/chat`, scaling-debt #11) —
out of scope for this item, tracked separately.
