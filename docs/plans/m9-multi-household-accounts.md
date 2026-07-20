# M9 — Multi-Household / Multi-User Accounts: Implementation Plan

> **Post-implementation note:** shipped closely following this plan — see
> `docs/roadmap.md`'s M9 entry for the full verification writeup. One real
> bug only surfaced by testing live against the already-running dev stack,
> not by the (fresh-fixture, every time) automated test suite: logging in
> against a household seeded before `password_hash` existed 500'd with a
> raw `KeyError`, since `ensure_seed_household` never retroactively patches
> an already-seeded household. Fixed in `aria_auth.passwords.verify_password`
> (widened to treat a missing/`None` stored hash as "fails to verify," not
> just a malformed string) and `routers/auth.py`'s `login()` (reads it via
> `.get()`). Everything else in this document reflects what actually shipped.

## Context

M1–M8 are done (`docs/roadmap.md`) — full CRUD tracking, document ingestion,
grounded/streaming/cited chat, multi-agent orchestration, and a write-capable
MCP action path. All of it sits on top of exactly **one** hardcoded
household and **one** hardcoded user (`core-api/app/seed.py`): login is a
single shared password (`CORE_API_ADMIN_PASSWORD`) that always resolves to
that same seeded "owner," regardless of what's typed as the identity. There
is no signup, no way to create a second household, and no way to add a
second person to the existing one. `User.role` and `check_permission()`
already exist (`libs/auth`, `docs/scaling-debt.md` #5) but the `PERMISSIONS`
registry they gate is empty, so the role distinction is currently inert.

This milestone turns that seam into a real feature:
- `POST /auth/signup` — create a brand-new household + owner user.
- A link-based invite flow to add a member to an *existing* household.
- Real per-user email+password login, replacing the single shared password.
- Concrete `PERMISSIONS` entries — the first real owner-vs-member
  restriction.
- **A per-record sharing model.** Today every household member can see and
  edit every entity/log/schedule/document in the household — sharing was
  never a real axis because there was never more than one user at all. Now
  that a household can genuinely have several members, each top-level
  record (entity, document) gets a `shared_with` setting: either the whole
  household (the default), or a specific subset of that household's
  members. Logs/schedules have no `shared_with` of their own — access to
  them is always derived from their parent entity's setting, since neither
  has any meaning outside the entity it's attached to. Regardless of
  sharing, the household **owner** always has full view/edit access to
  everything, and hard-**delete** of an entity/log/schedule stays
  owner-only exactly as already planned — sharing governs who else can
  view/edit, not who can delete.

**Explicitly bundled in, not a separate milestone:** the M4-era accepted
debt that Chroma retrieval is not scoped by household (`docs/roadmap.md`'s
M4 section: *"harmless today since `core-api/app/seed.py` seeds exactly one
household... revisit alongside [multi-household] work"*). Once a second
household can genuinely exist, an unscoped similarity search becomes a real
cross-household data leak — chat in household B could surface household A's
uploaded manuals. This has to close in the same milestone that makes a
second household possible, not after.

**Scope, narrowed up front (mirrors how M7/M8 narrowed their own scope):**
1. **One invite path: a shareable link/token, not email delivery.** No SMTP
   infra exists anywhere in this stack, and building one is disproportionate
   to the actual requirement (roadmap just says "add a member"). An owner
   generates an invite, gets back a token, and shares the resulting
   `/invite/{token}` URL out-of-band (text message, in person) — same
   "simplest thing that satisfies the requirement" instinct M6 applied to
   its hand-rolled `StreamFilter` and M8 applied to cookie-as-parameter
   tools.
2. **Only `role: "member"` invites.** An owner can't mint another owner via
   invite — avoids designing ownership transfer/multiple-owners semantics
   this milestone doesn't need. A household always has exactly one owner:
   whoever signed up.
3. **No password reset / forgot-password flow.** Real scope creep past
   "who can log in," and this is still a self-hosted, small-household tool
   — an owner who forgets their password today has direct Mongo access
   already assumed by the "single shared password" status quo. Flagged as
   deferred, not silently skipped.
4. **`PERMISSIONS` gets exactly one concrete role-based rule this milestone:
   hard delete is owner-only.** Every mutating route already calls
   `check_permission()` (that was the whole point of building the seam
   empty in M1), so this is a registry entry, not new router code.
   Archive/restore stay reversible and open to anyone with sharing access
   (`data-model.md`'s "soft delete") — only the irreversible hard-delete
   (a genuine M1 fast-follow-past-scope feature) is gated by role. Sharing
   (item 5 below) is the orthogonal axis that governs create/view/update
   access; role governs delete only. More granular role rules can be added
   to the same registry later without touching a router.
5. **Sharing is a single view+edit bucket, not separate view/edit levels.**
   A record is either visible-and-editable to a given member, or invisible
   to them entirely — no read-only shares this milestone. Keeps the access
   check a single boolean (`has_shared_access`) instead of a permission
   level, and nothing in the current product surface (household tracking
   among trusted co-residents) needs finer granularity yet.
6. **Chroma/RAG chat retrieval stays scoped at the household level only —
   it does not additionally respect per-document member-level sharing.**
   The household-scoping fix above (chunk metadata + backfill + Chroma
   `where` filter) is still required and unconditional. But teaching the
   *chat* retrieval path to also honor "this specific document is only
   shared with Alice, not Bob" would mean carrying `shared_with` into chunk
   metadata too and filtering per-request by the caller's `user_id`, not
   just their `household_id` — a meaningfully larger change to a read-only,
   already-low-risk-within-a-trusted-household code path. `core-api`'s own
   document endpoints (upload/list/get/download) *do* enforce per-document
   sharing (below) — only the chat-grounding shortcut through Chroma is
   scoped coarser, called out explicitly as a known gap rather than left
   undocumented.

---

## Key design decisions

**Password hashing: stdlib `hashlib.pbkdf2_hmac`, not a new dependency.**
No `bcrypt`/`passlib`/`argon2` package exists anywhere in this workspace
today. PBKDF2-HMAC-SHA256 at OWASP's current recommended iteration count
(600,000) is a legitimate, dependency-free choice already built into
Python's standard library — consistent with this codebase's repeated
preference for the simplest adequate tool over a new dependency (M6's
hand-rolled stream classifier over a parser library; M8's in-process tool
calls over a self-round-trip through MCP). Lives in a new
`libs/auth/src/aria_auth/passwords.py` (`hash_password(password) -> str`,
`verify_password(password, stored) -> bool`), not `core-api` directly,
since `aria_auth` is already this workspace's shared auth-primitives seam
(`session.py`, `permissions.py` live there for the same reason). Stored
format: `pbkdf2_sha256${iterations}${salt_hex}${hash_hex}` — the iteration
count travels with the hash so a future bump doesn't invalidate existing
hashes.

**Signup and invite-accept are the only two ways a `User` document gets
created; `ensure_seed_household` becomes just the first signup.** Today's
seed script inserts a `User` with no password at all (login never checked
per-user credentials). It now calls the same `hash_password()` and stores
it against `settings.admin_password` — so the existing dev/compose default
credentials (`owner@household.local` / `aria-dev`) keep working unchanged,
just through the real per-user login path instead of a bypass. No data
migration needed: this is still a pre-production, single-seeded-household
project (`docs/roadmap.md`'s own framing), so there's no real user data to
migrate off the shared-password model.

**Invite tokens are single-use and self-deleting, not flagged
`used_at`.** Matches this codebase's established preference for real
deletes over soft state where nothing needs the history (M1's "real
`DELETE`, not just archive," M8's TTL'd checkpoints) — once consumed, an
invite row has no further purpose. New `invites` collection:
`{_id: token, household_id, role, created_by_user_id, created_at,
expires_at}`. `token` is the Mongo `_id` directly (a `secrets.token_urlsafe`
value, same generation the session mechanism already uses) — no separate
lookup key needed.

**Owner-only household-management routes use a small dedicated
`require_owner` dependency, not a `PERMISSIONS` entry.** `check_permission`'s
shape is `(EntityDomain | None, Action) -> roles` — it exists to gate
*entity-domain* mutations (create/update/archive/restore/delete on a home/
vehicle/equipment/project/person). Inviting a member or listing a
household's users isn't a domain action at all; forcing it through that
dict would mean inventing a fake `domain=None` entry that reads as "delete
permission for entities" to anyone skimming `permissions.py` later. A
three-line `require_owner(session: SessionContext = Depends(get_current_session))`
dependency in `core-api/app/dependencies.py`, raising 403 if
`session.role != "owner"`, says exactly what it means at the one call site
that needs it (mirrors why M8 kept its cookie-forwarding tool parameter
separate from the session/DB machinery `core_api_client.py` already has —
different concern, different mechanism).

**Chroma household-scoping: filter at query time, backfill once, no
schema-version machinery.** `worker`'s embed step already has
`doc["household_id"]` in hand (`Document` has carried this field since M2);
it just never wrote it into chunk metadata. Going forward, every new chunk
gets `"household_id"` in its metadata dict alongside the existing
`mongo_document_id`/`page_number`/`chunk_index`/`section_header`
(`data-model.md`'s chunk-metadata shape). For chunks embedded before this
milestone, a one-off backfill script re-derives `household_id` per chunk
from its `mongo_document_id`'s `Document.household_id` and calls Chroma's
`collection.update()` — a single idempotent pass, not a versioned migration
framework, since this is a one-time closing of a known-narrow gap (today,
in practice, exactly the one seeded household's chunks) rather than an
ongoing schema-evolution concern.

`ai-service` never resolves its own `household_id` today — every read path
(`entity_grounding.py`, `core_api_client.py`) forwards the session cookie to
`core-api`, which derives `household_id` from the session server-side and
never hands the raw value back except via `GET /auth/me`. Chroma has no
concept of "the caller's session," so the literal id has to travel
client-side this time: `core_api_client.py` gains
`get_current_household_id(cookie) -> str | None` (a thin `GET /auth/me`
call, degrading to `None` on any failure — no cookie, expired session,
`core-api` down — exactly `fetch_entities()`'s existing 401-vs-other-failure
logging split). `routers/chat.py` resolves this once per request (alongside
where it already extracts the cookie) and threads it into
`config["configurable"]["household_id"]`, the same channel M7/M8 already use
to hand `cookie` to graph nodes without ever checkpointing it. Every node
that calls `retrieval.retrieve_context()` reads
`config["configurable"].get("household_id")` alongside the `cookie` it
already reads, and passes it through.

**No `household_id` → no document grounding, not unscoped search.**
`retrieval.retrieve_context(query, household_id)` only queries Chroma at all
when `household_id` is not `None`; otherwise it returns `[]` immediately,
same shape as `entity_grounding.gather_entity_context`'s existing "no
cookie → `[]`" contract. This is a deliberate behavior tightening, not a
regression: pre-M9, an unscoped query was the only option because no second
household existed to leak into; post-M9, degrading to *no* documents on a
missing/invalid session is the only correct choice — silently falling back
to "search everyone's documents" the moment auth is unavailable would be a
security regression hiding inside a decoupling feature.

**Sharing is one new field, not a new collection.** `EntityBase` and
`Document` each gain `shared_with: Literal["household"] | list[str]`
(a list of `user_id`s), default `"household"` — matching today's fully-open
behavior, so every pre-existing entity/document stays visible to the whole
household after this ships with no backfill needed (unlike the Chroma
household-scoping fix, there's no prior state to migrate: the default
*is* the prior behavior). A separate `shares` collection (row per
record-per-user) was considered and rejected — nothing here needs to query
"everything shared with user X across the household" independently of
already having a specific entity/document in hand, so a join-table only
adds a second place for sharing state to drift from the record it
describes.

**One access check, expressed over primitives, reused by both entities and
documents.** `libs/auth/src/aria_auth/sharing.py` (new):
```python
def has_shared_access(session: SessionContext, shared_with: str | list[str], owner_user_id: str) -> bool:
    if session.role == "owner":
        return True
    if session.user_id == owner_user_id:
        return True
    if shared_with == "household":
        return True
    return session.user_id in shared_with
```
Takes `shared_with`/`owner_user_id` as plain values rather than a raw Mongo
doc so one function serves `EntityBase.shared_with`/`created_by` and
`Document.shared_with`/`uploaded_by` without caring that the two models
name their "who made this" field differently. The owner-role check and the
creator check are both separate early-return branches *before* the
`shared_with` comparison — a creator who later narrows sharing to exclude
themselves can't accidentally lock themselves out, and the owner's access
never depends on being listed anywhere.

**Listing endpoints filter with a Mongo `$or`, not an in-memory
post-filter.** `{"$or": [{"shared_with": "household"}, {"shared_with":
session.user_id}, {"created_by": session.user_id}]}` added to
`list_entities`'s query (skipped entirely when `session.role == "owner"`,
who sees everything unfiltered). This relies on MongoDB's standard
scalar-or-array equality semantics: `{"shared_with": session.user_id}`
matches both a document where the field literally equals that string
(never happens here) and one where it's an array *containing* that value
— exactly the array-membership test needed, with no `$in`/`$elemMatch`
required. Single-record endpoints (`GET /entities/{id}`, `_require_document`)
instead fetch-then-check via `has_shared_access`, returning `404` (not
`403`) on failure — consistent with every existing `require_entity`/
`require_log`/`require_schedule` dependency's "wrong household" case,
which already 404s rather than 403s to avoid confirming a record's
existence to someone who can't see it.

**Only the record's owner-of-record (creator/uploader) or the household
owner can change `shared_with` itself.** Everyone with sharing access can
edit a record's content, but narrowing or widening *who else* can see it is
a more sensitive action reserved for whoever created it (or the household
owner as the administrative backstop) — otherwise any member with edit
access to a shared entity could unilaterally revoke every other member's
access to it, including the creator's. Detected via Pydantic v2's
`model_fields_set` on the update body (`"shared_with" in body.model_fields_set`)
so a `PATCH` that doesn't touch sharing at all never triggers this check.
Any explicit `shared_with` list (on create, or on this restricted update)
is validated server-side — every `user_id` in it must belong to
`session.household_id` (`db.users.count_documents({"_id": {"$in": ids},
"household_id": session.household_id})` must equal `len(ids)`), a 400
otherwise. This is the concrete mechanism behind "only with members in the
same household" — not just a UI affordance that only lists same-household
members, but a server-side invariant, the same defense-in-depth stance the
rest of this API already takes (e.g. `require_entity_for_create` re-checking
an entity belongs to the caller's household rather than trusting the
client-supplied `entity_id`).

**Logs and schedules have no `shared_with` of their own — access is
computed against their parent entity, once, at the same dependency that
already fetches it.** `require_log`/`require_schedule`/
`require_entity_for_create` already fetch the parent entity doc (or the
log/schedule doc directly, which carries `entity_id`) before doing
anything else; adding `has_shared_access(session, entity_doc["shared_with"],
entity_doc["created_by"])` to that same dependency costs one extra field
read, not a new query. This mirrors the existing precedent that logs/
schedules only ever carry `domain` (for `check_permission`) by copying it
from their entity at creation time, rather than duplicating entity state
independently — the difference here is `shared_with` is looked up live from
the entity each time rather than copied, since (unlike `domain`) it's
meant to change over the entity's lifetime and every log/schedule should
see the current value, not a stale copy from whenever it was created.

---

## File-by-file plan

### `libs/shared/src/aria_shared/models/household.py`
- `User` gains `password_hash: str`.

### `libs/shared/src/aria_shared/models/entities/__init__.py`
- `EntityBase` gains `shared_with: Literal["household"] | list[PyObjectId] = "household"`.

### `libs/shared/src/aria_shared/models/documents.py`
- `Document` gains `shared_with: Literal["household"] | list[PyObjectId] = "household"`
  (same shape and default as `EntityBase`'s, independent of it — a document
  linked to a fully-shared entity can still be narrowed on its own, e.g. a
  receipt with pricing the creator doesn't want visible to every member).

### `libs/auth/src/aria_auth/`
- `passwords.py` (new): `hash_password(password: str) -> str`,
  `verify_password(password: str, stored: str) -> bool` (constant-time
  comparison via `hmac.compare_digest` on the derived key, same discipline
  `routers/auth.py` already uses for the shared password today).
- `sharing.py` (new): `has_shared_access(session: SessionContext, shared_with: str | list[str], owner_user_id: str) -> bool`
  per the "Key design decisions" section above.
- `__init__.py` — export `hash_password`, `verify_password`, `has_shared_access`.
- `tests/test_passwords.py` (new): round-trip hash/verify, wrong password
  rejected, malformed stored-hash string rejected (not raises).
- `tests/test_sharing.py` (new): owner always `True` regardless of
  `shared_with`/creator; creator always `True` even if excluded from a
  narrowed `shared_with` list; `"household"` grants any member `True`; a
  member not in an explicit `shared_with` list and not the creator gets
  `False`.

### `services/core-api/app/`
- `config.py` — add `invite_ttl_hours: int = 24 * 7`. `admin_password`'s
  docstring/comment updated: now the seeded owner's *initial password*, not
  a bypass for all logins.
- `seed.py` — `ensure_seed_household` calls `hash_password(settings.admin_password)`
  and stores it as the seeded `User.password_hash`.
- `dependencies.py`:
  - New `require_owner(session: SessionContext = Depends(get_current_session)) -> SessionContext`,
    raising `403` if `session.role != "owner"`.
  - New `async def validate_shared_with(db, household_id: str, shared_with: str | list[str]) -> None` —
    no-op when `shared_with == "household"`; otherwise 400s unless
    `db.users.count_documents({"_id": {"$in": shared_with}, "household_id": household_id})`
    equals `len(shared_with)`. Shared by `entities.py`'s and `documents.py`'s
    create/update handlers so the "same household only" invariant has one
    implementation.
  - `require_entity_for_create` (used by both `logs.py` and `schedules.py`)
    gains a `has_shared_access` check right after its existing
    `check_permission` call, 403ing if the resolved entity isn't shared
    with the caller — a member can't log against or schedule against an
    entity they can't otherwise see or edit.
- `routers/auth.py`:
  - `LoginRequest` gains `email: str`; `login()` looks up
    `db.users.find_one({"email": body.email})`, 401s if missing, verifies
    `verify_password(body.password, user["password_hash"])` instead of
    comparing against `settings.admin_password` — the single-shared-password
    branch is deleted, not kept as a fallback.
  - New `SignupRequest{household_name, name, email, password}` and
    `POST /auth/signup`: 409 if `email` already registered; otherwise
    inserts a new `Household` + owner `User` (mirrors `ensure_seed_household`'s
    shape) in the same two-insert pattern, calls `create_session()`, sets
    the cookie, returns `SessionResponse` — auto-login on signup, same as
    accept-invite below.
- `routers/households.py` (new):
  - `POST /households/invites` (`Depends(require_owner)`) — body
    `{}` (role is always `"member"`, per scope note 2); creates an `invites`
    doc, `expires_at = now + invite_ttl_hours`; returns `{token, expires_at}`.
  - `GET /households/invites` (`Depends(require_owner)`) — list pending
    invites for `session.household_id` (for a revoke UI).
  - `DELETE /households/invites/{token}` (`Depends(require_owner)`) —
    deletes the invite doc if it belongs to the caller's household (404
    otherwise); real delete, not archive, matching the collection's
    single-use nature.
  - `GET /households/members` (`Depends(get_current_session)`, any role) —
    `db.users.find({"household_id": session.household_id})` projected to
    `{id, name, email, role}`.
  - `AcceptInviteRequest{token, name, email, password}` and
    `POST /auth/accept-invite` (public, lives here since it's
    household-scoped even though unauthenticated — same reasoning `auth.py`
    already applies to `/login`): fetch the invite by token, 404/410 if
    missing/expired; 409 if `email` already registered; insert a new `User`
    under `invite["household_id"]` with `role="member"`; delete the invite
    doc; `create_session()`; set cookie; return `SessionResponse`.
- `permissions.py` (via `libs/auth`, see below) — no router changes needed
  for delete-gating; every delete route already calls
  `check_permission(session.role, domain, "delete")`.
- `main.py` — wire the new `households` router.
- `schemas/entities.py` — `EntityCreate`/`EntityUpdate` gain
  `shared_with: Literal["household"] | list[str] | None = None` (`None` on
  `EntityUpdate` means "not being changed," per `exclude_unset`; `EntityCreate`
  defaults to `"household"` like the model itself).
- `routers/entities.py`:
  - `require_entity(action)` — after its existing fetch + `check_permission`,
    adds `if not has_shared_access(session, doc["shared_with"], doc["created_by"]): raise 404`
    (not 403 — see "Key design decisions"). Covers `update`/`archive`/
    `restore`/`delete`'s shared fetch path in one place; `delete`'s own
    `check_permission(..., "delete")` (owner-only) still runs first via the
    same dependency, so a non-owner gets the 403 role error before this 404
    would even be reached, which is fine — either response correctly denies
    the request.
  - `list_entities` — query gains the `$or` sharing filter (skipped when
    `session.role == "owner"`), per "Key design decisions."
  - `get_entity` — after the existing household-scoped fetch, adds the same
    `has_shared_access` check, 404 on failure.
  - `create_entity` — `EntityBase(...)` gains `shared_with=body.shared_with or "household"`;
    calls `await validate_shared_with(db, session.household_id, entity.shared_with)`
    before the insert.
  - `update_entity` — if `"shared_with" in body.model_fields_set`: 403 unless
    `session.role == "owner" or session.user_id == current.created_by`; then
    `await validate_shared_with(...)` on the new value before merging.
- `routers/logs.py`:
  - `require_log(action)` — after fetching the log, also fetches its parent
    entity (`db.entities.find_one({"_id": doc["entity_id"], "household_id": session.household_id})`)
    and applies `has_shared_access` against *that* entity doc, 404 if absent —
    a log's own record carries no `shared_with` of its own (see "Key design
    decisions").
  - `list_entity_logs` — fetches the entity first (as it already does),
    adds the same `has_shared_access` check (404 on failure) before querying
    `db.logs.find(...)`.
- `routers/schedules.py` — mirrors `logs.py` exactly: `require_schedule(action)`
  resolves the parent entity and applies `has_shared_access`;
  `list_entity_schedules` does the same before its query.
- `schemas/documents.py` — `DocumentUploadMeta` gains
  `shared_with: Literal["household"] | list[str] = "household"`.
- `routers/documents.py`:
  - `upload_document` — after the existing per-entity `check_permission`
    loop, `await validate_shared_with(db, session.household_id, meta.shared_with)`;
    `Document(...)` gains `shared_with=meta.shared_with`.
  - `_require_document` — after its existing fetch, adds
    `has_shared_access(session, doc["shared_with"], doc["uploaded_by"])`,
    404 on failure — covers `get_document`/`download_document`/
    `delete_document`'s shared fetch path in one place (delete's own
    per-linked-entity `check_permission(..., "delete")` — owner-only via the
    `PERMISSIONS` fallback — still runs independently inside the handler).
  - `list_entity_documents` — fetches the entity first (as it already does)
    and adds `has_shared_access` against it (404 on failure, same as
    `list_entity_logs`); the returned document list is additionally filtered
    to only documents where `has_shared_access(session, doc["shared_with"],
    doc["uploaded_by"])` — being able to see the entity doesn't automatically
    mean every document attached to it is shared with you too (a document's
    `shared_with` can be narrower than its linked entity's).
- `tests/`: `test_auth_signup.py` (new household created, duplicate-email
  409), `test_auth_login.py` (rewritten for email+password, wrong password,
  unknown email), `test_invites.py` (create/accept happy path, expired
  invite rejected, non-owner can't create an invite, accept consumes the
  token so a second accept 404s), `test_permissions_delete.py` (member 403s
  on delete, owner succeeds — extends the existing entity/log/schedule
  archive tests rather than duplicating their fixtures), `test_sharing.py`
  (new: an entity shared with a specific member list is 404 to a
  same-household member not on it and visible to one who is; `list_entities`
  excludes/includes accordingly; a non-creator/non-owner member gets 403
  patching `shared_with` but can still patch other fields; `validate_shared_with`
  rejects a user id from a different household; logs/schedules inherit
  their entity's current `shared_with`, including after it changes;
  document-level sharing narrower than its linked entity's is enforced on
  `list_entity_documents`).

### `libs/auth/src/aria_auth/permissions.py`
- `PERMISSIONS[(None, "delete")] = frozenset({"owner"})`. Comment updated:
  this is no longer a documented-but-empty seam — it now has its first real
  entry, and the reasoning for why it's `(None, ...)` rather than
  per-domain (a household's hard-delete risk is the same regardless of
  which domain the record belongs to) replaces the old "empty today"
  comment.

### `services/worker/app/tasks/process_document.py`
- The chunk-metadata dict built before `chroma.get_documents_collection().add(...)`
  gains `"household_id": doc["household_id"]`.
- `services/worker/app/backfill_household_id.py` (new, one-off, run via
  `docker compose run --rm worker python -m app.backfill_household_id`):
  iterates every `documents` record, queries the Chroma collection with
  `where={"mongo_document_id": doc_id}` to find that document's existing
  chunks, and — only for chunks whose metadata is missing `household_id` —
  calls `collection.update(ids=..., metadatas=[...])` with the field added.
  Idempotent (a re-run touches zero already-backfilled chunks); logs a
  summary count of documents/chunks touched.
- `tests/test_backfill_household_id.py` (new): a fake Chroma collection
  fixture confirming already-tagged chunks are left untouched and
  untagged ones are updated with the correct id.

### `services/ai-service/app/`
- `core_api_client.py` — new `async def get_current_household_id(cookie: str) -> str | None`,
  wrapping `GET /auth/me` in the same try/except-with-401-vs-other-logging
  split `entity_grounding.fetch_entities()` already established (factor that
  split out to a small shared helper if it turns out identical in both
  places — check during implementation rather than presupposing it here).
- `retrieval.py` — `retrieve_context(query: str, household_id: str | None) -> list[RetrievedChunk]`:
  returns `[]` immediately if `household_id is None`; otherwise passes
  `where={"household_id": household_id}` into the existing
  `collection.query(...)` call.
- `agents/nodes.py` — every call site that reads
  `cookie = config["configurable"].get("cookie")` also reads
  `household_id = config["configurable"].get("household_id")` and forwards
  it to `retrieval.retrieve_context`/`gather_baseline_context` (which gains
  the same new parameter, threaded straight through to its own
  `retrieve_context` call). Applies to `gather_baseline_context`,
  `_gather_household_and_documents`, `research_node`, and
  `propose_action_node`'s baseline-gather branch — four call sites, all
  mechanical.
- `routers/chat.py` — `_route_and_gather` and `_resume_action` both resolve
  `household_id = await core_api_client.get_current_household_id(cookie)`
  once, alongside where `cookie` is already available, and add it to the
  `config["configurable"]` dict passed into `graph.astream(...)`.
- `tests/test_retrieval.py` — extend for the `household_id=None` short-circuit
  and the `where` filter being passed through to the fake Chroma collection.
- `tests/test_agents.py`/`test_chat.py` — extend existing fixtures to pass a
  `household_id` through `config["configurable"]` and confirm it reaches the
  fake retrieval call.
- No entity/document *sharing* logic is added anywhere in `ai-service`.
  `entity_grounding.py`'s `GET /entities`/`.../logs`/`.../schedules` calls and
  `mcp_tools.py`'s `create_log`/`create_schedule` all forward the caller's
  cookie straight to `core-api`, which now enforces sharing server-side —
  chat grounding and agent-proposed writes both automatically respect a
  member's sharing restrictions with zero code changes here, the same
  "core-api already scopes it" free ride M8 got from household scoping.

### `services/frontend/src/`
- `api/auth.ts` — `login(email, password)` (signature change, not additive —
  every caller updates); new `signup(householdName, name, email, password)`,
  `acceptInvite(token, name, email, password)`.
- `api/households.ts` (new) — `listMembers()`, `createInvite()`,
  `listInvites()`, `revokeInvite(token)`.
- `api/types.ts` — new `Member`, `Invite` types; `Entity`/`Document` gain
  `shared_with: 'household' | string[]`, matching this file's existing
  convention of mirroring the wire's snake_case rather than remapping to
  camelCase.
- `hooks/useSession.ts` — `useLogin`'s `mutationFn` signature updates to
  `(email, password) => login(email, password)`; new `useSignup`,
  `useAcceptInvite` (both mirror `useLogin`'s `onSuccess` — seed the
  `['session']` query cache directly, since signup/accept-invite also
  return a fresh `SessionResponse`).
- `hooks/useHousehold.ts` (new) — `useMembers`, `useInvites`,
  `useCreateInvite`, `useRevokeInvite` (react-query, invalidating
  `['invites']` on create/revoke). `useMembers` is also the data source for
  the sharing picker below, not just the Profile page.
- `components/SharingControl.tsx` (new) — the one sharing-picker component
  reused by `EntityForm` and `DocumentUploadForm`: a "Whole household" /
  "Specific members" radio, and — only in the latter mode — a checklist of
  `useMembers()`'s result (excluding the current user, who always has
  access per `has_shared_access` and doesn't need to see themselves as a
  togglable option). One component so the two forms can't drift on how
  this control looks or behaves.
- `components/EntityForm.tsx` — includes `SharingControl`, wired to a new
  `shared_with` form field; on an *edit* of an existing entity, the control
  is only rendered enabled if `session.user_id === entity.created_by ||
  session.role === 'owner'` (read-only display otherwise), mirroring
  `core-api`'s own restriction on who may change it.
- `components/DocumentUploadForm.tsx` — same `SharingControl`, wired into
  the existing upload form's payload (`shared_with` alongside
  `document_type`/`entity_ids`).
- `components/StatusBadge.tsx` or a small sibling — a "Shared with 2
  members" / "Shared with household" indicator shown on `EntityDetailPage`
  and the document list, read-only display only (editing always happens
  through the form).
- `pages/LoginPage.tsx` — add an email input alongside the existing password
  input; add a "Create a household" link to `/signup`.
- `pages/SignupPage.tsx` (new) — household name + name + email + password
  form, same layout/card style as `LoginPage`; on success, session cache is
  populated and the page redirects to `/` (mirrors `LoginPage`'s existing
  `Navigate` pattern).
- `pages/AcceptInvitePage.tsx` (new) — reads `:token` from the route; name +
  email + password form; on submit, calls `acceptInvite`, seeds the session
  cache, redirects to `/`. An invalid/expired token renders an inline error
  instead of the form (no retry — the owner has to issue a new invite).
- `pages/ProfilePage.tsx` — new "Household members" card below the existing
  "Signed in as"/"Theme" cards: lists members (name, email, role badge);
  if `session.role === 'owner'`, also shows an "Invite a member" button that
  calls `useCreateInvite`, then renders the resulting `/invite/{token}` URL
  in a read-only input with a copy-to-clipboard button, plus a list of
  outstanding invites with a Revoke action per row.
- `App.tsx` — `/signup` and `/invite/:token` added as public routes
  alongside `/login`, outside `RequireAuth`.

---

## Sequencing

1. `libs/shared` (`User.password_hash`) and `libs/auth` (`passwords.py` +
   its unit tests) — no other layer depends on these existing yet, so they
   land and get verified in isolation first.
2. `core-api`, accounts: config, `seed.py`'s hashed seed password,
   `dependencies.py`'s `require_owner`, `routers/auth.py` rewritten for
   email+password + signup, `routers/households.py` (invites + members +
   accept-invite), `PERMISSIONS[(None, "delete")]`, `main.py` wiring, all
   new/updated tests. Verify manually via `/docs` + curl: signup a second
   household, log in as its owner, confirm the seeded household's owner can
   still log in with today's default credentials, create+accept an invite,
   confirm a member gets 403 on hard-delete while the owner succeeds.
3. `core-api`, sharing: `libs/shared`'s `shared_with` field on
   `EntityBase`/`Document`, `libs/auth`'s `sharing.py` +
   `dependencies.py`'s `validate_shared_with`/`require_entity_for_create`
   extension, then `routers/entities.py` → `routers/logs.py`/`schedules.py`
   → `routers/documents.py` in that order (each layer's access check
   depends on the previous one existing to test against). Verify manually:
   as a household with 2+ members (use the invite flow from step 2), create
   an entity shared with only one specific member, confirm a third member
   gets `404` on it via both `GET /entities/{id}` and `GET /entities`'s
   list, confirm logging against it 403s/404s for an excluded member, and
   confirm only the creator or owner can change its `shared_with` (an
   included-but-not-creator member gets 403 attempting to).
4. `worker`: chunk-metadata `household_id` write (new documents only,
   verify by uploading a fresh document post-change and inspecting its
   chunks' metadata directly against the Chroma HTTP API), then the
   backfill script against the pre-existing seeded household's chunks —
   verify old chunks retrieve identically before and after.
5. `ai-service`: `core_api_client.get_current_household_id`, `retrieval.py`'s
   new parameter + Chroma `where` filter, the four `agents/nodes.py` call
   sites, `routers/chat.py` threading `household_id` into
   `config["configurable"]`. Extend/verify tests. Verify live: two real
   households (the seeded one + a freshly-signed-up one), each with its own
   uploaded document, confirm chat in each only ever surfaces its own
   household's document content — this is the milestone's core proof for
   household scoping (sharing between members of the *same* household was
   already verified in step 3; this step is the cross-household boundary).
6. Frontend: `api/auth.ts`/`households.ts`/`types.ts`, the two new hooks
   files, `SharingControl`, `LoginPage` email field, `SignupPage`,
   `AcceptInvitePage`, `EntityForm`/`DocumentUploadForm`'s sharing picker,
   the "shared with" detail-page indicator, `ProfilePage`'s members card,
   `App.tsx` routes. `npm run lint`/`npm run build` clean.
7. Manual browser walkthrough end-to-end (below).

---

## Verification

**Manual walkthrough (happy path):**
- Sign up a brand-new household ("Household B") with its own owner
  credentials — confirm auto-login lands on `/`, distinct from the seeded
  household.
- As Household B's owner: upload a document distinct from anything in the
  seeded household; ask chat a question only that document answers —
  confirm it's grounded and cited correctly.
- Log back into the original seeded household (default credentials) and
  ask the same question — confirm it is **not** grounded in Household B's
  document (the cross-household leak this milestone exists to close).
- As Household B's owner, generate an invite from the Profile page, copy
  the link, open it in a fresh/incognito session, fill out the accept-invite
  form — confirm a new `role: "member"` user is created under Household B
  (not the owner's household) and lands auto-logged-in on `/`.
- As that member, attempt to hard-delete an entity — confirm 403 and that
  archive still works; log back in as the owner and confirm hard-delete
  succeeds.
- Confirm an expired or already-consumed invite link shows a clear error,
  not a broken form.

**Sharing walkthrough (within one household, 3 members — owner + 2 invited
members, "Member A" and "Member B"):**
- As Member A, create an entity with sharing narrowed to just Member A +
  the owner (excluding Member B). Confirm: Member B doesn't see it in their
  entity list and gets a 404 opening it directly by URL/id; the owner sees
  it in their list despite not being explicitly listed; Member A can add a
  log entry and a schedule against it, and both are visible to the owner
  but invisible to Member B (`GET /entities/{id}/logs` 404s for Member B
  the same way the entity itself does).
- Still as Member A, try to give Member B access after the fact by editing
  the entity's sharing to include them — confirm Member B can now see it
  (and its existing logs/schedules) immediately, no re-creation needed.
- As Member B, upload a document narrowed to just Member B + the owner
  and linked to a household-wide-shared entity — confirm Member A can see
  the *entity* (it's household-shared) but not this specific *document* on
  it, while the owner can see both.
- As the owner, confirm you can view, edit, and hard-delete Member A's
  narrowly-shared entity from step 1 without ever having been explicitly
  added to its `shared_with` — the owner-override path.
- As Member B (not the creator, not the owner) on an entity that *is*
  shared with them, confirm you can edit its ordinary fields (name, status,
  tags) but get 403 attempting to change its `shared_with`.
- Attempt (via a raw API call, not the UI, since the UI's member picker
  wouldn't offer this) to share an entity with a user id from a *different*
  household — confirm `core-api` 400s rather than silently accepting it.

**Degrade-path walkthrough:**
- Stop `core-api` mid-chat-session — confirm `get_current_household_id`'s
  failure degrades `retrieval.retrieve_context` to `[]` (no documents, not
  an unfiltered/cross-household result) exactly like the existing
  entity-grounding degrade path, and that ordinary chat still returns `200`.
- Confirm `docker compose up --build` still boots clean and `core-api`'s
  own CRUD (M1) is entirely unaffected by any of the above for a
  household/session that never touches invites or narrows sharing at all —
  every pre-existing entity/document defaults to `shared_with: "household"`,
  so a single-member household sees zero behavior change.

**Automated:** `uv run pytest` in `libs/auth`, `core-api`, `worker`, and
`ai-service` (new/extended suites listed per file above); `npm run lint` +
`npm run build` in `frontend`. As with every prior milestone, the actual
Profile-page invite UI and accept-invite form are verified by the manual
walkthrough only.

### Critical files
- `libs/shared/src/aria_shared/models/household.py`
- `libs/shared/src/aria_shared/models/entities/__init__.py`
- `libs/shared/src/aria_shared/models/documents.py`
- `libs/auth/src/aria_auth/passwords.py`
- `libs/auth/src/aria_auth/sharing.py`
- `libs/auth/src/aria_auth/permissions.py`
- `services/core-api/app/seed.py`
- `services/core-api/app/routers/auth.py`
- `services/core-api/app/routers/households.py`
- `services/core-api/app/routers/entities.py`
- `services/core-api/app/routers/logs.py`
- `services/core-api/app/routers/schedules.py`
- `services/core-api/app/routers/documents.py`
- `services/core-api/app/dependencies.py`
- `services/worker/app/tasks/process_document.py`
- `services/worker/app/backfill_household_id.py`
- `services/ai-service/app/core_api_client.py`
- `services/ai-service/app/retrieval.py`
- `services/ai-service/app/agents/nodes.py`
- `services/ai-service/app/routers/chat.py`
- `services/frontend/src/components/SharingControl.tsx`
- `services/frontend/src/components/EntityForm.tsx`
- `services/frontend/src/pages/ProfilePage.tsx`
- `services/frontend/src/App.tsx`
