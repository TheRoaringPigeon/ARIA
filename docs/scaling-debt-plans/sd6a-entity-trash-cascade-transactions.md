# Entity trash cascade transactions

Covers half of [`scaling-debt.md`](../scaling-debt.md) item **#6** — the
`delete_entity`/`restore_entity_from_trash` half. The other half (worker's
`purge_expired_trash` batching) shipped first, independently, in
[`sd6b-purge-sweep-batching.md`](sd6b-purge-sweep-batching.md).

## Context

`services/core-api/app/routers/entities.py`'s `delete_entity` and
`restore_entity_from_trash` each did 3 sequential, un-transacted writes
across `entities`/`logs`/`schedules`. A crash or timeout between them left
an entity trashed while its logs/schedules stayed live and visible via the
list endpoints — a real, silent state-inconsistency bug, not just a
performance concern.

Mongo ran as a bare standalone in both compose files (`image: mongo:7`, no
`--replSet`) — multi-document ACID transactions are categorically
unavailable on a standalone; this is a hard server-side requirement, not a
driver setting. `docs/plans/ok-let-s-take-a-starry-cat.md:88-97` already
explicitly deferred wrapping a similar multi-write in a transaction for
exactly this reason, flagging replica-set init as a fast-follow — this is
that fast-follow. See `CLAUDE.md`'s "Mongo write patterns" section for the
general shape of when a Mongo problem needs real transactions (this one)
versus just batching (sd6b's problem).

## Design

### Mongo: single-node replica set

Both `docker-compose.yml` and `docker-compose.prod.yml`: `mongo` gets
`command: ["mongod", "--replSet", "rs0", "--bind_ip_all"]`, plus a new
one-shot `mongo-init` service that runs `rs.initiate()` exactly once via
`mongosh --eval` (checks `rs.status()` first, only initiates on
`NotYetInitialized` — idempotent across restarts since the replica-set
config persists in the data volume). `core-api`/`worker`/`worker-beat` each
depend on `mongo-init: condition: service_completed_successfully` in
addition to `mongo: condition: service_healthy` — connecting with
`replicaSet=rs0` before `rs.initiate()` has run leaves the driver stuck
discovering a primary that doesn't exist yet, so gating on `mongo-init`
avoids that hang. `CORE_API_MONGO_URI`/`WORKER_MONGO_URI` (and the matching
`mongo_uri` defaults in `services/core-api/app/config.py` /
`services/worker/app/config.py`) changed from `mongodb://mongo:27017` to
`mongodb://mongo:27017/?replicaSet=rs0`. No auth/keyfile needed — Mongo runs
with no auth today, and a single-node replica set with no auth needs no
keyfile either.

### `services/core-api/app/db.py` — transaction helper

```python
@asynccontextmanager
async def start_transaction() -> AsyncIterator[AsyncIOMotorClientSession]:
    client = get_client()
    async with await client.start_session() as session:
        async with session.start_transaction():
            yield session
```

Motor's own documented usage shape for `start_session()`/`start_transaction()`.

### `services/core-api/app/dependencies.py` — testability seam

`get_transaction_dep()` returns the `start_transaction` factory itself (not
a live session) as a FastAPI dependency, so tests can override it the same
way `get_current_session` already is (`app.dependency_overrides`) rather
than monkeypatching internals.

### `services/core-api/app/routers/entities.py`

`delete_entity` and `restore_entity_from_trash` each add
`start_txn = Depends(get_transaction_dep)`, wrap their existing 3 writes in
`async with start_txn() as txn:`, and pass `session=txn` on each call. The
`require_entity(...)` permission/existence read stays outside the
transaction — this item is about the writes not being atomic with each
other, not read-consistency of the permission check.

## Test infra

mongomock's `AsyncMongoMockClient` doesn't implement `start_session()` at
all — confirmed directly (`start_session()` raises `NotImplementedError`).
Per-call `session=` kwargs are silently accepted everywhere else, so only
session/transaction *creation* is the gap:

- `tests/conftest.py` gained an autouse `mock_transaction` fixture
  overriding `get_transaction_dep` with a no-op async context manager
  (`yield None`). Existing tests (`test_entities_delete.py` etc.) pass
  unchanged since mongomock ignores `session=None`.
- New `test_db_transactions.py` unit-tests `start_transaction()`'s control
  flow against a small hand-rolled fake client: proves it calls
  `client.start_session()` then `session.start_transaction()` in order,
  yields the session, and — critically — lets an exception raised inside the
  block propagate rather than swallowing it (the signal a real Mongo session
  needs to abort instead of commit). Also a trivial test that
  `get_transaction_dep()` resolves to the real `start_transaction` in
  non-test wiring, not a permanent stub.

This is a substitute for true rollback proof, which mongomock can't give —
real atomicity was verified live instead (below).

## Verification

- 226 `core-api` tests pass (rebuilt image, since `tests/` isn't
  bind-mounted the way `app/` is — a plain `docker compose exec` was running
  a stale baked-in copy of the test suite before the rebuild).
- Fresh replica-set init from a clean volume: recreated `mongo`/`mongo-init`
  against the real dev stack (`docker compose up -d --build mongo mongo-init
  core-api worker worker-beat`) — `mongo-init` logged `{ ok: 1 }` and exited
  cleanly, `rs.status().ok` returned `1`, `core-api`/`worker` both started
  normally on the new `replicaSet=rs0` URI. Separately, to confirm this
  isn't an artifact of the existing dev volume already having replication
  metadata, ran the same `mongod --replSet rs0` + `rs.initiate()` sequence
  against a throwaway, never-before-used container/volume outside the dev
  stack — same clean `{ ok: 1 }` / `rs.status().ok === 1` result.
- **Real rollback, not just the no-op override**: temporarily inserted
  `raise RuntimeError(...)` between `logs.update_many` and
  `schedules.update_many` in `delete_entity`, created a scratch vehicle
  entity plus a log and schedule referencing it, hit `DELETE
  /entities/{id}` against the live stack — got a `500`, then inspected all
  three collections directly via `mongosh`: `entities.pending_delete_at`,
  `logs.pending_delete_at`, and `schedules.pending_delete_at` were all still
  `null` — including on the `entities` and `logs` writes that ran *before*
  the forced exception. That's the actual bug this item fixes: those two
  writes would previously have silently persisted despite the failure.
  Removed the temporary raise, reran the same request — got `204`, and all
  three documents now carried the identical `pending_delete_at` timestamp,
  confirming the normal (non-aborted) path still commits everything
  together. Scratch data cleaned up afterward.

## Production migration (not part of this change's automated rollout)

Prod (`docker-compose.prod.yml`) already runs with real household data in
`mongo_data_prod`. This change does not touch prod directly — deploying it
there requires:

1. **Backup first** — `mongodump` or a volume snapshot of `mongo_data_prod`.
   Standard due diligence, not optional.
2. Deploy the updated compose file. Recreates `mongo` with `--replSet`
   added, which requires a restart — brief availability gap (mongod stops,
   restarts with the flag, `mongo-init` runs `rs.initiate()` once, then
   `core-api`/`worker`/`worker-beat` come up on the new `replicaSet=rs0` URI
   as part of the same redeploy). Budget a couple of minutes of downtime,
   done at low traffic.
3. **Reversible**: existing data isn't converted or migrated, only newly
   replica-set-initiated — a replica-set mongod can be restarted *without*
   `--replSet` and functions as a standalone again if something goes wrong,
   so rollback is a normal revert-and-restart. The backup from step 1
   remains the safety net regardless.

This is a manual, deliberate operational step for the user to run against
prod on their own schedule — not something to execute automatically as part
of merging this code.
