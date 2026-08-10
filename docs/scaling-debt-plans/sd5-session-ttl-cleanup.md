# Session TTL cleanup

Covers [`scaling-debt.md`](../scaling-debt.md) item **#5** — `sessions`
grows without bound because nothing ever sweeps expired rows.

## Context

`libs/auth/src/aria_auth/session.py:22-53` (`create_session`) writes one
`sessions` document per login with an application-level `expires_at`
(`ttl_hours` from each service's own config — core-api is the only real
caller today, confirmed by grep: no other service touches `db.sessions`
directly, they only forward the `aria_session` cookie to core-api).
`build_get_current_session` (`session.py:70-96`) only ever deletes a row
lazily, the one time its *own* already-expired token happens to be
re-presented (`session.py:85`); `POST /auth/logout`
(`routers/auth.py:161`) deletes exactly the one row for the session that
logged out. Nothing deletes a session that simply expires and is never
presented again — the overwhelmingly common case (a browser tab closed, a
device not reused within the 7-day TTL) — so `sessions` only ever grows.

This was explicitly scoped *out* of the #1/#2 indexing pass
([`sd1-2-mongo-indexing-and-signup-race.md`](sd1-2-mongo-indexing-and-signup-race.md#scope)):
the query shape (`find_one({"_id": ...})`) is already covered by Mongo's
automatic `_id` index — the actual problem is unbounded growth of an
unindexed-beyond-`_id`, never-swept collection that's read on every
authenticated request, not a missing lookup index.

## Design

One-line fix in `services/core-api/app/indexes.py`, in the same
`ensure_indexes()` function and idempotent-`create_index` style as every
existing index:

```python
# sessions: no lookup index needed (find_one is already _id-indexed), but
# nothing ever deletes an expired row that's never re-presented — the
# common case. A TTL index lets Mongo's own background sweep reclaim these
# instead of the collection growing forever. expireAfterSeconds=0 means
# "expire exactly at the timestamp stored in the field" (expires_at is
# already an absolute datetime, not a relative duration).
await db.sessions.create_index("expires_at", expireAfterSeconds=0)
```

No other code changes. The existing lazy delete-on-presented-expired-token
path (`session.py:85`) and `/auth/logout`'s explicit delete both stay as-is
— they're still correct and still the *fast* path for the two cases they
cover (a stale token gets reused, a user explicitly logs out); the TTL
index only closes the gap for sessions that are never presented again.

**Not doing:** a Celery Beat sweep task analogous to `purge_expired_trash`.
Mongo's own TTL background thread already does exactly this job
server-side, checked every 60s by default — a duplicate application-level
sweep would be redundant, not complementary.

## Verification

- Extend `services/core-api/tests/test_indexes.py` with
  `test_ensure_indexes_creates_sessions_ttl_index`, following the existing
  per-collection pattern: assert `expires_at_1` is present in
  `db.sessions.index_information()` with `expireAfterSeconds == 0`.
- New test proving the TTL actually reclaims rows, not just that the index
  exists: insert a session document with `expires_at` in the past, call
  `ensure_indexes`, then assert it's gone. `mongomock` (verified in
  `.venv/Lib/site-packages/mongomock/store.py:89-160`) actually simulates
  TTL expiry against wall-clock time on document access, so this is
  testable without a real Mongo instance — no need to mock time.
- Regression: existing `test_auth.py` login/logout/session tests still
  pass — the TTL index doesn't change behavior for any non-expired session.
- Manual, via the `verify` skill against the real running stack: confirm
  `db.sessions.getIndexes()` (mongosh) shows the new TTL index with
  `expireAfterSeconds: 0` after a fresh `core-api` startup. Actually
  observing Mongo's background sweep fire isn't practical to verify live
  (its default check interval is ~60s and it's not something `explain()`
  surfaces), so this step confirms the index is correctly configured, not
  that a real expired row got swept — the mongomock test above covers that.
