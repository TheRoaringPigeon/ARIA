# Purge sweep batching

Covers half of [`scaling-debt.md`](../scaling-debt.md) item **#6** — the
`purge_expired_trash` half. The other half (`delete_entity`/
`restore_entity_from_trash` cascade transactionality) is a separate,
higher-risk change tracked in
[`sd6a-entity-trash-cascade-transactions.md`](sd6a-entity-trash-cascade-transactions.md)
and ships independently.

## Context

`services/worker/app/tasks/purge_expired_trash.py` (the hourly Celery Beat
sweep that permanently deletes entities past their trash grace period) did
6+ sequential Mongo round trips *per expired entity* inside a plain Python
`for` loop: `delete_one`, `delete_many` × 2, `find`, `update_many` × 2, plus a
per-referencing-document orphan check. Cost scaled linearly with concurrent
trash volume across *every* household sharing the hourly run, and this task
shares a Celery queue with OCR (item #20) — a busy trash day and a busy
upload day compete for the same worker slots.

This is a pure round-trip-count problem, not a correctness/atomicity one —
each individual write was (and remains) a single atomic per-document Mongo
operation; nothing here ever needed cross-collection transactions. See
`CLAUDE.md`'s new "Mongo write patterns" section for why that distinction
matters and is worth checking before reaching for heavier fixes.

## Design

Replaced the per-entity loop with a fixed ~8 round trips total, independent
of how many entities are expired in a given run:

1. `find` all entities matching the expired filter → snapshot of candidate
   ids.
2. `delete_many` on `entities`, re-applying the *same* expired filter
   (`{"_id": {"$in": ids}, "pending_delete_at": {"$ne": None, "$lt": cutoff}}`)
   rather than trusting the snapshot — Mongo evaluates this per-document at
   write time, so a household member restoring an entity between the
   snapshot and here still correctly excludes it. This is the exact
   race-safety guarantee the old per-entity `delete_one` had, just applied to
   the whole batch in one round trip instead of one delete per entity.
3. `find` the candidate ids again to see what's still present, diff against
   the snapshot to get `purged_ids` — `delete_many` only returns a count, not
   which ids matched, so this is how "what actually got purged" is recovered
   without looping.
4. `delete_many` on `logs`/`schedules` with `{"entity_id": {"$in": purged_ids}}`
   — no `household_id` filter. `entity_id` is globally unique
   (`app/ids.py`'s `new_id()` → `str(ObjectId())`, not namespaced by
   household), and `logs`/`schedules`/`documents.entity_ids`/
   `users.pinned_entity_ids` all reference it directly — confirmed by reading
   the actual field usage in `routers/logs.py`, `routers/schedules.py`,
   `routers/documents.py`. Safe to batch across every household sharing this
   hourly run in one call each.
5. `find` referencing documents, `update_many` to pull every purged id from
   `entity_ids` in one call, then re-read the same referencing set to find
   newly-orphaned documents and enqueue `delete_document.delay(...)` for
   each — unchanged from before (Celery enqueues, not a DB round-trip
   concern), just driven off the batch instead of one entity's ids at a
   time.
6. `update_many` on `users` pulling every purged id from `pinned_entity_ids`
   in one call, no per-household filter needed for the same reason as step 4.

Used plain batched `update_many`/`delete_many`, not `bulk_write` — every
operation here already applies one uniform filter+update to every matching
document in a single command; `bulk_write` only earns its keep when different
documents need different operations in the same call, which nothing here
needs.

## Test infra changes

`services/worker/tests/fakes.py`'s hand-rolled `FakeCollection` only
implemented `find_one`/`find`/`insert_one`/`update_one` ($set only, single
match)/`delete_one` — `purge_expired_trash` had zero test coverage before
this. Added:

- `update_many`/`delete_many`, applying to every matching doc.
- `$pull` support in `_apply_update` (including `$pull: {field: {"$in": [...]}}`).
- Array-containment matching in `_matches` — a bare-equality or `$in` query
  against a list-valued field (e.g. `documents.entity_ids`) now matches on
  element containment, not scalar/whole-list comparison. This was silently
  wrong before (nothing exercised it), and would have broken the
  `documents.entity_ids` queries in the new tests without this fix.

`delete_one`'s existing raise-on-no-match behavior was left untouched (other
callers still rely on it as an assertion; the rewritten task no longer calls
`delete_one`).

## Verification

New `services/worker/tests/test_purge_expired_trash.py`:

- Full cascade for a single expired entity (log, schedule, pin, and a
  document shared with a still-alive entity) — entity/logs/schedules gone,
  document survives with only the purged id pulled, no orphan-delete
  enqueued, pin removed.
- An orphaned document (linked only to the purged entity) — asserts
  `delete_document.delay(doc_id, storage_path)` is actually called.
- Restore race — monkeypatches the fake's `delete_many` to flip one
  candidate's `pending_delete_at` back to `None` right before the real
  delete runs (simulating a restore landing between the snapshot and the
  batch write); asserts that entity, and only that entity, survives the
  whole cascade untouched while a second, genuinely-expired entity in the
  same run is fully purged.
- No expired entities → `{"purged": 0}`, and asserts (via monkeypatched
  collections that raise if called) that no other collection is touched at
  all.
- Multi-entity, multi-household batch (3 expired entities across 2
  households in one run) — all 3 fully purged, no cross-household
  contamination of unrelated users/documents.

All 5 new tests pass; full `services/worker` suite (46 tests) passes.
