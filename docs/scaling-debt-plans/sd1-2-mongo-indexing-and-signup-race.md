# Mongo indexing pass + signup/accept-invite race fix

Covers [`scaling-debt.md`](../scaling-debt.md) items **#1** (only index in
the system, `users.email` unindexed + signup TOCTOU race) and **#2**
(`logs`/`schedules`/`documents` also unindexed on their hot query shapes) —
bundled into one plan since #1 itself calls out #2 as the natural next step,
and both are the same class of fix touching the same file
(`services/core-api/app/indexes.py`).

## Context

`services/core-api/app/indexes.py` has exactly one index in the whole
system: a compound `(household_id, archived_at, domain)` on `entities`,
created idempotently in `main.py`'s `lifespan` via `ensure_indexes(get_db())`
on every startup. Nothing else — `users`, `logs`, `schedules`, `documents`,
`sessions`, `invites` — has any index beyond Mongo's automatic one on `_id`.
`services/core-api/tests/test_indexes.py` already establishes the test
pattern (`mongomock_motor`'s `AsyncMongoMockClient` supports
`create_index`/`index_information()`, so index creation is testable without
a real Mongo instance).

Two concrete, verified problems this plan fixes:

1. **`/auth/login` and `/auth/signup` full-scan `users` on every call** —
   the single highest-frequency request path in the app
   (`routers/auth.py:79,102`) — because nothing indexes `email`.
2. **`/auth/signup` and `/auth/accept-invite` have a real TOCTOU race**:
   both do `find_one({"email": ...})` then, if `None`, `insert_one(...)` a
   few lines later (`routers/auth.py:102-127`,
   `routers/households.py:236-249`). Two concurrent requests with the same
   email can both pass the check and both insert — two `User` documents
   sharing one email, which `find_one({"email": ...})` then resolves
   ambiguously (whichever Mongo happens to return first) for every future
   login attempt against that address.

Plus the other hot, currently-unindexed query shapes surfaced in the same
audit (`scaling-debt.md` #2): `logs`' schedule-resync lookup (runs on every
log create/update/delete that touches a `schedule_id`), the
entity-scoped log/schedule/document list endpoints, and the
due-soon/calendar/overdue-digest schedule query shape that's shared by three
different call sites (two `core-api` endpoints plus the worker's daily
digest task).

## Scope

**In scope** — new indexes on `users`, `logs`, `schedules`, `documents`, and
the signup/accept-invite race fix.

**Explicitly out of scope** (separate, already-numbered items in
`scaling-debt.md`, not touched here):
- **#5**, session TTL/cleanup — `sessions` needs no new index for the query
  shapes fixed here (`sessions.find_one({"_id": ...})` is already covered by
  Mongo's automatic `_id` index); the *growth* problem is a missing
  TTL/sweep, a different fix.
- **#6**, the trash-cascade purge sweep's `entities.find({"pending_delete_at":
  {"$ne": None, "$lt": cutoff}})` (`worker/app/tasks/purge_expired_trash.py:24`)
  is a real unindexed *global* (not household-scoped) query, but it belongs
  to the trash-cascade item, not this indexing pass on `users`/`logs`/
  `schedules`/`documents`.
- **#7**, the `$regex` entity search/type-ahead — a different indexing
  strategy entirely (text index or a search-specific data structure, not a
  plain compound index), called out as its own item.
- `invites` — re-checked while researching this plan: its only query shapes
  are `find_one({"_id": body.token})` (accept-invite lookup, already
  `_id`-indexed) and `find({"household_id": ...})` (list pending invites,
  small per-household N, not currently a hot path). Named in #2's header but
  not actually a concrete problem on inspection — no index added here.

## Design

### New indexes (`services/core-api/app/indexes.py`)

Extend `ensure_indexes()` with one `create_index` call per collection,
grouped and commented the same way the existing `entities` index is —
field order leads with the equality filter every call site shares
(`household_id` where applicable), range/sort fields last.

```python
async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.entities.create_index([("household_id", 1), ("archived_at", 1), ("domain", 1)])

    # users.email: unique — enforces at the database level what the
    # application-level check-then-insert in routers/auth.py and
    # routers/households.py (signup, accept-invite) can only do
    # probabilistically. Also the index that makes /auth/login and
    # /auth/signup's duplicate check an index lookup instead of a full
    # collection scan (the single highest-frequency query path in the app).
    await db.users.create_index("email", unique=True)
    # household member listing (routers/households.py list_members).
    await db.users.create_index("household_id")
    # worker's daily overdue-digest task scans for opted-in users; most
    # users have this False, so a partial index only covers the ones that
    # matter. Falls back to a plain (non-partial) index if the installed
    # mongomock_motor version doesn't support partialFilterExpression in
    # tests — verify during implementation.
    await db.users.create_index(
        "notify_overdue_email",
        partialFilterExpression={"notify_overdue_email": True},
    )

    # logs: entity-scoped history (list_entity_logs, PDF export) and the
    # schedule-resync lookup (_resync_schedule, runs on every log
    # create/update/delete with a schedule_id) are two distinct access
    # patterns needing two distinct compound indexes — neither is a
    # prefix of the other.
    await db.logs.create_index([("household_id", 1), ("entity_id", 1), ("occurred_at", -1)])
    await db.logs.create_index([("household_id", 1), ("schedule_id", 1), ("occurred_at", -1)])

    # schedules: entity-scoped list (list_entity_schedules) is a separate
    # access pattern from the due-soon/calendar/digest shape below — the
    # latter's leading fields (active, pending_delete_at, interval_type)
    # aren't present on the former's query at all, so one compound index
    # can't serve both.
    await db.schedules.create_index([("household_id", 1), ("entity_id", 1)])
    # Serves three call sites that all share this exact equality-filter
    # shape: GET /schedules/due-soon, GET /schedules/calendar (core-api),
    # and worker's send_overdue_digest — household_id/active/
    # pending_delete_at/interval_type are equality filters on every one of
    # them; next_due_at trails as the field due-soon/the digest task also
    # range-filter and sort on (calendar doesn't filter on it, but a
    # trailing extra field is harmless when unused).
    await db.schedules.create_index(
        [
            ("household_id", 1),
            ("active", 1),
            ("pending_delete_at", 1),
            ("interval_type", 1),
            ("next_due_at", 1),
        ]
    )

    # documents: entity_ids is many-to-many (array field) — Mongo indexes
    # this as a multikey index automatically, no special syntax needed.
    # Serves list_entity_documents and the PDF-export document pull.
    await db.documents.create_index(
        [("household_id", 1), ("entity_ids", 1), ("uploaded_at", -1)]
    )
```

### Signup / accept-invite race fix

Both routes get the same shape: keep the existing pre-check (still a useful
fast-path 409 for the overwhelmingly common non-racing case — no wasted
round trip to discover a real conflict), but also catch
`pymongo.errors.DuplicateKeyError` around the `insert_one` and convert it to
the identical `409 "email already registered"` the pre-check already
returns. First real use of `pymongo.errors` in this codebase — verified no
existing import convention to match.

`routers/auth.py::signup`:

```python
from pymongo.errors import DuplicateKeyError

...

    if await db.users.find_one({"email": body.email}) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    now = datetime.now(timezone.utc)
    household_id = new_id()
    user_id = new_id()

    await db.households.insert_one({...})  # unchanged
    user = {...}  # unchanged
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError:
        # Lost a race against a concurrent signup/accept-invite for the same
        # email between the check above and this insert. The household
        # document above was already written — an orphaned household with
        # no user is harmless (nothing lists/logs into a household without
        # a member who can log in) and matches this codebase's existing
        # "worst case is a recoverable leftover, not a corrupted state"
        # tolerance (see logs.py's create_log comment on schedule-resync
        # ordering for the same philosophy).
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None
```

`routers/households.py::accept_invite` gets the identical
try/except around its own `insert_one(user)`. The invite-delete
(`db.invites.delete_one(...)`) stays *after* the insert, unchanged — if the
insert raises, the invite is deliberately left intact rather than consumed,
so the loser of the race can retry with a different email against the same
still-valid invite link.

`app/seed.py::ensure_seed_household` is **not** touched — it runs once,
synchronously, in `lifespan` before the app starts accepting requests, so
there's no concurrent caller to race against. Its existing check-then-insert
is fine as-is.

### Rollout note (no code, but worth doing before deploying)

`create_index(..., unique=True)` fails at creation time if the collection
already contains duplicate values — and `ensure_indexes()` runs
unconditionally in `lifespan`, so a genuine pre-existing duplicate would
fail the whole app's startup, not just this one index. Given every insert
path already does an application-level uniqueness check today (this plan is
closing the *race window* around that check, not adding the check itself),
a real duplicate existing in either the dev or prod database is very
unlikely but not provably impossible. Before deploying, run once against
each real database (not needed for local mongomock-backed tests, which
always start empty):

```js
db.users.aggregate([
  { $group: { _id: "$email", count: { $sum: 1 } } },
  { $match: { count: { $gt: 1 } } },
])
```

If this ever returns a non-empty result, resolve the duplicate manually
before deploying this change — not something to build automatic handling
for, since it would mean investigating how two accounts ended up sharing
credentials in the first place.

## Verification

- Extend `services/core-api/tests/test_indexes.py` (mirrors the existing two
  tests) with one assertion per new index — name and `key` list via
  `index_information()` — for `users` (all three), `logs` (both), and
  `schedules` (both), `documents`. Reuse the existing
  `test_ensure_indexes_is_idempotent` pattern (call `ensure_indexes` twice,
  assert no error and the index is still there) rather than writing a
  second idempotency test per collection.
- New test in `services/core-api/tests/test_auth.py`: after calling
  `ensure_indexes(mock_db)`, insert a user directly, then attempt a second
  `db.users.insert_one` with the same email and assert `mongomock_motor`
  raises `DuplicateKeyError` — proves the unique index itself actually
  enforces uniqueness under the test double, not just in real Mongo.
- New test exercising the route-level race handling specifically (since a
  single-threaded test can't produce a real concurrent race): monkeypatch
  `db.users.find_one` for the *pre-check* call only to return `None` (as if
  the race window were open) while a colliding user document already exists
  in `mock_db.users` from a real prior insert — call `POST /auth/signup`
  with that email and assert `409`, not an unhandled `500` propagating the
  raw `DuplicateKeyError`. Repeat the same shape for `POST
  /auth/accept-invite`.
- Regression check: existing `test_auth.py`/`test_households.py` cases for
  the *non-racing* 409 (plain duplicate-email signup, no monkeypatching)
  still pass unchanged — the pre-check still fires first in the common
  case, so behavior for an already-existing test should be identical.
- Manual, via the `verify` skill against the real running stack: confirm
  `db.users.getIndexes()` (mongosh) shows all three new indexes after a
  fresh `core-api` startup; confirm `explain()` on a `/auth/login` call
  shows an `IXSCAN` on `email` instead of `COLLSCAN`; confirm normal
  login/signup/accept-invite/create-log/create-schedule/list-entity-logs/
  list-entity-schedules/list-entity-documents flows all still work
  end-to-end (these are the query shapes the new indexes cover — a wrong
  field order or a typo'd field name wouldn't break the query, just fail to
  speed it up, so a functional pass alone doesn't fully verify the index is
  correct — pair it with the `explain()` check above).
