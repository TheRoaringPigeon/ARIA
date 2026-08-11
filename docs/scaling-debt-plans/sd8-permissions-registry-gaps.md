# PERMISSIONS enforcement gaps: document drafts, and closing the orphaned-document race at the root

Covers [`scaling-debt.md`](../scaling-debt.md) item **#8** — `PERMISSIONS`
(`libs/auth/src/aria_auth/permissions.py`) only has two entries (owner-only
hard-delete/undelete), flagged as a decision point for onboarding
less-trusted household members later.

## Scope decision

A repo-wide check found no product spec anywhere for what a third role
(roommate/tenant/contractor) should actually be restricted from doing —
those words only appear in the debt item's own hypothetical framing. Per
the user's explicit scope call, this fix does **not** invent a new role or
add any new restriction to `create`/`update`/`archive`/`restore`. Instead it
closes two concrete enforcement gaps found while researching the item:
places where the `check_permission()` seam described as "already built"
actually wasn't wired in at all. The broader "should a future role tier be
restricted from more actions" question is recorded as a deliberately
deferred decision in
[`scaling-decisions.md`](../scaling-decisions.md#2--a-third-household-member-role-tier-beyond-ownermember),
same split pattern item #7 used.

## The two gaps

1. **Document drafts had zero permission checks.**
   `services/core-api/app/routers/documents.py`'s five draft-mutation
   routes (`upload_draft_page`, `delete_draft_page`, `reorder_draft_pages`,
   `finalize_document_draft_endpoint`, `cancel_document_draft`) all depended
   only on `_require_draft`, which checked sharing but never called
   `check_permission`. Every other mutating resource (entities, logs,
   schedules, single-file documents) already goes through a
   `require_<resource>(action)`-style factory or an explicit
   `check_permission` call — drafts were the one resource type where the
   mechanism was never wired in, not just "wired in but unrestricted."

2. **`delete_document`/`rename_document` skipped the check entirely when
   `entity_ids` was empty.** Both handlers guarded their per-linked-entity
   `check_permission` loop with `if document.entity_ids:` — when empty, the
   loop (and the check) was skipped, not "checked against zero domains."
   `entity_ids` is guaranteed non-empty at *creation* time
   (`DocumentUploadMeta`/`DocumentDraftCreateMeta` both reject an empty list
   via `field_validator`), so this was purely a post-creation state.

## Gap 2's fix changed after a design review — worth recording why

The first pass at gap 2 treated the empty-`entity_ids` state as legitimate
and added a fallback: `check_permission(session.role, None, "delete"/
"update")` when the list was empty, instead of skipping. That was based on
an incomplete read of `purge_expired_trash.py` — the assumption was that
once an entity is hard-deleted, any document that was only linked to it
becomes a permanently orphaned row (unlinked forever, never cleaned up).

That assumption was wrong, and user review caught it: `purge_expired_trash`
(`services/worker/app/tasks/purge_expired_trash.py`) already detects
exactly this case (a document whose `entity_ids` — and `log_ids`, always
empty in practice since nothing populates it today — both end up empty
after the unlink) and enqueues `delete_document.delay(doc_id,
storage_path)` for it, which deletes the Mongo row too, not just S3/Chroma.
So a document was never actually orphaned forever — it was already being
cleaned up. The real gap was narrower: the entity-unlink (`documents
.update_many`, stripping the id) happened *inside* the Mongo transaction,
but the row deletion happened *after* the transaction committed, via an
**async Celery enqueue**. Between those two moments — however long it takes
the worker to pick up the task off Redis, longer under load since it shares
a queue with OCR (`scaling-debt.md` #20) — the document genuinely sat in
Mongo with `entity_ids: []`, reachable by a normal API call. That's a real
but narrow race window, not a permanent state, and defending it with a
wildcard permission check was solving the wrong layer: it added a special
case for a state that shouldn't be observable at all, rather than closing
the window that let it become observable.

**The actual fix:** move the orphaned-document row deletion *into* the same
Mongo transaction as the entity-unlink, so `entity_ids: []` is never a
persisted, queryable state — not even briefly. With that invariant genuinely
holding, `delete_document`/`rename_document` don't need any empty-list
handling at all; they're reverted to the plain per-linked-entity-domain
loop, now with a comment explaining why the list is guaranteed non-empty.

## Design

### `services/worker/app/tasks/purge_expired_trash.py`: delete orphaned documents in-transaction

Previously: unlink (`update_many` with `$pull`) inside the transaction,
compute which referencing documents ended up empty (`orphaned_docs`), then
*after* the transaction commits, `delete_document.delay(doc_id,
storage_path)` per orphaned doc — that Celery task does the Mongo
`delete_one` too.

Now: right after computing `orphaned_docs` (still inside the transaction),
`db.documents.delete_many({"_id": {"$in": [...]}}, session=txn)` removes
the Mongo rows atomically with the unlink. The post-transaction
`delete_document.delay(...)` loop is unchanged in shape — it still fires for
S3/Chroma cleanup — but its own `documents.delete_one` becomes a no-op
against an already-missing row, which the task's docstring already
documents as a safe, expected case ("every step here is a no-op on an
already-missing target").

### `services/core-api/app/routers/documents.py`: revert to trusting the invariant

`delete_document`/`rename_document` no longer branch on whether
`entity_ids` is empty — the per-linked-entity-domain `check_permission`
loop just runs unconditionally, with a comment noting `entity_ids` is never
empty for a document reachable here, because of the transactional delete
above. This matches this repo's stated convention (`CLAUDE.md`) of not
adding defensive handling for states that structurally can't occur.

### `documents.py`: new `require_draft(action)` factory (unchanged from the first pass)

Added alongside the existing bare `_require_draft` (kept, unchanged, for
the two read-only draft routes — reads were never gated by `PERMISSIONS`
for any other resource either). The factory fetches the draft via
`_require_draft`, then calls `check_permission(session.role,
entity_doc["domain"], action)` for each of the draft's linked entities
(`entity_ids` is non-empty by construction and never mutated after create,
so there's no empty-list case to consider here in the first place).

All five mutating draft routes depend on `require_draft("update")`.
Finalize and cancel are mapped to `"update"` rather than `"create"`/
`"delete"` — a user decision made explicitly to avoid ever accidentally
owner-gating "cancel my own in-progress mobile photo capture" if `"delete"`
semantics were ever extended further, since a draft isn't yet a real
persisted household record the way a finalized `Document` is. Verified live
that a member can still cancel their own draft (see Verification).

### No new `PERMISSIONS` entries

The registry itself is unchanged. This fix is entirely about making sure
`check_permission` is *called* everywhere a mutation happens (drafts) and
that the states it's called against are real (documents) — since nothing
new restricts `"update"`, no existing test's expected status code changes
for the common case.

### `permissions.py` comment, `scaling-decisions.md`

The module comment notes that a short registry isn't itself a gap — the
enforcement mechanism is complete (every mutating route calls
`check_permission`, including drafts as of this fix); populating it further
is a deferred product decision, not unfinished plumbing. A new entry in
`scaling-decisions.md` records that decision: today's role model is
`owner`/`member` only, sharing (not role) governs most access per the M9
plan's explicit stance, and no product language anywhere defines a third
role's semantics — this is a policy question blocked on product design, not
an engineering gap, and the mechanism is ready the moment it's answered.

## Tests

`services/core-api/tests/test_permissions_draft_gaps.py` (7 tests, renamed
from `test_permissions_registry_gaps.py` once it narrowed to draft-only
coverage): a member can still complete the full draft lifecycle (create,
upload, finalize) and cancel their own draft under today's unrestricted
registry (no behavior change for the common case); for each of the five
mutating routes, a temporary `PERMISSIONS` entry proves a disallowed role
now gets `403` where it previously would have silently succeeded.

`services/worker/tests/test_purge_expired_trash.py`: extended
`test_orphaned_document_enqueued_for_deletion` to also assert the Mongo row
is gone immediately after `purge_expired_trash()` returns (not just that
`delete_document.delay` was called), plus a new
`test_orphaned_document_deleted_even_if_delay_enqueue_never_runs` proving
the row deletion doesn't depend on the Celery enqueue succeeding — it's a
plain write inside the transaction.

`test_permissions.py`/`test_permissions_delete.py` needed no changes — no
new permanent `PERMISSIONS` entries were added. Full `core-api` suite: 245
tests pass (238 baseline + 7 new draft tests). Full `worker` suite: 50 tests
pass (46 baseline + 2 new transaction tests, plus 2 pre-existing from a
prior fix).

## Verification

Verified live against the real running stack (`docker compose`), not just
the test suites:

- Confirmed `core-api` hot-reloaded `documents.py` and
  `libs/auth/.../permissions.py`, and `worker` picked up
  `purge_expired_trash.py`, cleanly with no import/startup errors.
- Created a second real member account in the dev seed household via a
  real invite/accept-invite round trip (not a test double), alongside the
  seeded owner account. As the member: created a draft, uploaded a real
  JPEG page, and finalized it — all succeeded (`200`/`202`), confirming the
  newly-added `check_permission` calls don't change behavior for the
  unrestricted common case. Created and canceled a second draft as the same
  member — `204`, confirming "cancel my own draft" stays available (the
  reason `"delete"` was deliberately not used for that action).
- Reproduced the real orphaning path end-to-end, not a simulated one:
  uploaded a document linked to a single scratch entity, soft-trashed that
  entity (`DELETE /entities/{id}`), backdated its `pending_delete_at` past
  the grace period via `mongosh`, then ran `purge_expired_trash()`
  synchronously inside the worker container (calling the Celery task
  function directly, not through the broker). Confirmed via `mongosh`
  immediately afterward — no wait, no polling — that both the entity and
  the document row were already gone; confirmed `GET /documents/{id}`
  returns `404` through the real API. No intermediate `entity_ids: []`
  state was ever observable.
- All scratch data (entities, documents, the second member account)
  removed afterward — entities via the app's existing trash/delete paths,
  or already gone via the purge run itself; the scratch member account via
  direct cleanup (no remove-member endpoint exists) — dev seed household
  only, not the real household.
