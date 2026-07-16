# Scaling Debt

Design decisions that work fine at today's scale (5 entity domains, single
developer, no auth enforcement) but will get more expensive to live with as
the app grows — more entity types, more contributors, more UI variants. This
is a tracking list, not a plan: each item needs its own design decision
before it's fixed. Written 2026-07-15, after the entity-domain-registry
refactor (commit `9f20952`).

Status legend: 🔴 not started · 🟡 partially addressed · 🟢 fixed

---

## 1. 🟢 Entity schema is hand-duplicated across Python and TypeScript

**Fixed:** Python remains the source of truth
(`libs/shared/src/aria_shared/models/entities/*.py`); a generator
(`entities/export_ts.py`) introspects `ENTITY_DOMAINS` and each `*Attrs`
class's `model_fields` and writes real generated TypeScript
(`services/frontend/src/domains/generated.ts` — per-domain interfaces,
`ENTITY_DOMAINS`, `VALID_STATUSES`/`LOG_TYPES` as `GENERATED.<domain>`, and
the global `LogType` union). Regenerate with:

```
uv run --package aria-shared python -m aria_shared.models.entities.export_ts \
  --out services/frontend/src/domains/generated.ts
```

The 5 hand-written `domains/<domain>.ts` files now import their attrs type
and `statuses`/`logTypes` from `generated.ts`, keeping only genuinely
UI-only concerns (field `label`, `kind`, placeholders, `defaultAttributes`).
`FieldConfig<TAttrs>` narrows `key` to the domain's real field names, so a
renamed/typo'd field is a `tsc` error. `domains/index.ts`'s `DOMAIN_REGISTRY`
is typed `Record<GeneratedEntityDomain, ...>`, so an unregistered or
mismatched domain is also a `tsc` error. `libs/shared/tests/test_export_ts.py`
fails `pytest` if the committed `generated.ts` is stale relative to the
Pydantic models — this is the drift check (no CI configured yet, so it's
enforced by running the test locally, same as any other `pytest` failure).

Note: `VALID_STATUSES`/`LOG_TYPES` were `ClassVar[set[str]]`; generating from
a `set` would have scrambled the array order `EntityForm.tsx`/`LogForm.tsx`
rely on for default form values (`statuses[0]`), so they were first
converted to `ClassVar[tuple[str, ...]]` (order-preserving, no behavior
change).

**Still open:** the flat `LogType` union (not scoped per domain — see #4)
and the UI-only `FieldConfig` metadata (label/kind/placeholder) are
unaffected by this fix; #2/#3's `domain === 'person'` branching is a
separate issue.

---

## 2. 🟢 `domain === 'person'` branches leak outside the domain registry

**Fixed:** added `uiVariant: 'schedule' | 'plan'` to `DomainConfig`
(`domains/base.ts`), set per domain in each `domains/<domain>.ts` file
(`person.ts` → `'plan'`, the other four → `'schedule'`). All five call sites
now read `DOMAIN_REGISTRY[domain].uiVariant` instead of comparing the
domain string:

- `services/frontend/src/pages/EntityDetailPage.tsx` — tab label, and the
  two `tab === 'schedules'` guard conditions selecting the Plans vs
  Schedules block (`usesPlansUI` derived once from the registry)
- `services/frontend/src/components/LogForm.tsx` — `showCostAndSchedule`/
  `showMetrics` and the title placeholder, all derived from
  `DOMAIN_REGISTRY[domain].uiVariant`

A future domain wanting Plans-style UI now just sets `uiVariant: 'plan'` in
its config — no call-site edits. `tsc --noEmit` passes clean.

**Still open:** the two JSX blocks in `EntityDetailPage.tsx` remain
structurally duplicated (button text, empty-state copy, `PlanForm` vs
`ScheduleForm`) — collapsing them into one is item #3.

---

## 3. 🟢 `PlanForm.tsx` / `ScheduleForm.tsx` are near-duplicate components

**Fixed:** `PlanForm.tsx` deleted; `ScheduleForm.tsx` now takes a
`variant: 'plan' | 'schedule'` prop and drives both UIs off one component
(shared `title`/`mode`/interval-field state and `handleSubmit`, with
per-variant copy and layout — see `COPY` and the `isPlan` branches).
`RecurrenceMode` (`lib/recurrence.ts`) grew `'once'` and `'usage'` so
`recurrenceModeOf`/`describeRecurrence` cover every `interval_type` a
`Schedule` can have, not just the plan-side subset. `EntityDetailPage.tsx`
picks the variant from `DOMAIN_REGISTRY[domain].uiVariant` (see #2).

Auditing the merge surfaced two real CRUD gaps, closed alongside it rather
than left as new debt:
- **Schedules (non-plan) had no Edit/Delete UI** — the backend already
  supported `PATCH`/`DELETE /schedules/{id}` (shared `Schedule` resource),
  only the frontend never wired it for the non-plan variant. The non-plan
  schedule list in `EntityDetailPage.tsx` now has the same edit/delete
  interaction pattern the plan list already had.
- **Entities had no hard-delete anywhere** — only archive/restore existed
  (`data-model.md` §9 only ever designed archival as the soft-delete
  mechanism). Added `DELETE /entities/{entity_id}`
  (`routers/entities.py`), which cascades to delete that entity's logs and
  schedules too — unlike schedule deletion, which deliberately leaves a
  referencing log's `schedule_id` dangling because the entity+log stay
  viewable, deleting the entity removes the only place its logs/schedules
  could ever be viewed from, so leaving them behind would just be
  unreachable Mongo orphans. Frontend: `useDeleteEntity`, a "Delete" button
  on `EntityDetailPage` next to Archive/Restore (always visible, distinct
  destructive-confirm wording), navigates to `/entities` on success.

**Still open:** nothing new — Logs and Plans already had full Edit+Delete
before this pass; this closes the last two gaps (Schedules, Entities) so
all four object types now have consistent CRUD.

---

## 4. 🟡 Global flat `LogType` union, not scoped per domain

**Where:** `libs/shared/src/aria_shared/models/logs.py:9-20` (the Python
`Literal[...]`) and `services/frontend/src/domains/generated.ts`'s
`LOG_TYPES`/`LogType` (generated from it — see #1; `api/types.ts` now just
re-exports `LogType` from `domains`) — a single flat union listing every log
type across every domain.

**Partially fixed (frontend only):** `export_ts.py` now also generates
`LogTypeFor<D extends GeneratedEntityDomain>` (`(typeof GENERATED)[D]['logTypes'][number]`)
in `generated.ts`. `DomainConfig<TAttrs, TDomain>` (`domains/base.ts`) takes
the domain literal as a second type param and types `logTypes` as
`readonly LogTypeFor<TDomain>[]`; each `domains/<domain>.ts` config now
passes its own literal domain (e.g. `DomainConfig<HomeAttrs, 'home'>`), so a
copy-pasted `logTypes: GENERATED.person.logTypes` inside `home.ts` is now a
`tsc` error instead of silently compiling. `DOMAIN_REGISTRY`'s value type
still defaults `TDomain` to the full domain union, since `DOMAIN_REGISTRY[domain]`
is always looked up with a runtime (non-literal) `domain`, so this doesn't
narrow anything at the generic call sites (`LogForm.tsx`,
`EntityDetailPage.tsx`) — the app has no place where `domain` is a
compile-time literal outside the `domains/<domain>.ts` files themselves.

**Still open:** the Python side is unchanged — `LogEntry.type: LogType` is
still a flat `Literal[...]`, and `_check_type_valid_for_domain` is still the
only thing stopping a `home` log from using a `person`-only type at
request-validation time. A full fix (discriminated union mirroring
`EntityAttributes`, e.g. via dynamically generated per-domain `LogEntry`
variants) was considered but rejected for now: `LogEntry` would stop being
directly constructible (`LogEntry(...)` in `routers/logs.py:create_log`
would need to become a `TypeAdapter` call), and the OpenAPI schema shape
would change — too invasive for a currently-low-value, not-urgent item.
Revisit if domain count grows enough that the Python-side gap starts
actually causing bugs.

---

## 5. 🟢 No permission/role enforcement seam

**Fixed, two passes.** The first pass threaded `role` end-to-end and added
an inline `check_permission()` call to every mutating handler. The second
pass (below) is what actually shipped: it extracts the whole seam into a new
workspace package, `libs/auth` (`aria_auth`), and converts every permission
check from an imperative call inside a handler body into a real FastAPI
`Depends()` — both in anticipation of `ai-service`/`worker` eventually
needing the same session/role logic (docs/data-model.md already reserved
`User.role` for this), and because a `Depends()`-based check runs (and can
403) before the handler body executes, instead of partway through it.

**`libs/auth`** (added to the root `uv.workspace`, depends on `aria-shared`
for `Role`/`EntityDomain`) holds everything session- and permission-related
that used to live in `core-api/app/`:

- `SessionContext`, `SESSION_COOKIE_NAME`, `create_session()` (now takes
  `ttl_hours` as a parameter instead of reading a service's settings
  directly — each service still owns its own config).
- `build_get_current_session(get_db)` — a **dependency factory**, not a
  hardcoded `Depends()`. `aria_auth` has no way to reach into any one
  service's Motor client singleton, so each service calls this once (see
  `core-api/app/dependencies.py`: `get_current_session =
  build_get_current_session(get_db_dep)`) and every router imports that one
  bound function. That single object is also what makes test overrides
  (below) work everywhere at once.
- `Action`, `PERMISSIONS: dict[tuple[EntityDomain | None, Action],
  frozenset[Role]]`, `check_permission()` — unchanged in shape from the
  first pass, just relocated. Still starts **empty**: no product
  requirement yet says who can do what, so this remains behaviorally a
  no-op (every route falls through to the permissive default). A future
  restriction is still one registry line, not a router change.
- `libs/auth/tests/` covers the registry logic and session mechanics
  (expiry, missing cookie, legacy sessions with no `role` key) in isolation,
  independent of any one service's FastAPI app.

**`Depends()`, not inline calls.** Each of `entities.py`/`logs.py`/
`schedules.py` now defines resource-specific dependency factories —
`require_entity(action)`, `require_log(action)`, `require_schedule(action)`
— that fetch the path's `{x_id}` (404 if missing/wrong household), call
`check_permission()` against its domain (403 if disallowed), and return the
doc for the handler to use. A mutating route like:

```python
async def update_entity(
    entity_id: str,
    body: EntityUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("update")),
) -> EntityBase:
    ...
```

gets 404 and 403 handled entirely by the dependency graph — no
fetch/if-404/check_permission block at the top of the handler body anymore.
Creation is the one case with no existing resource to fetch a domain from;
those routes (`create_entity`, and the log/schedule routes that resolve
permission from their parent entity) use `dependencies=[Depends(...)]` on
the route decorator or a body-parsing dependency (`require_entity_create_permission`,
`require_entity_for_log_create`, `require_entity_for_schedule_create`) —
FastAPI parses a Pydantic body model used in both a dependency and the
endpoint exactly once and shares it, confirmed empirically before relying
on it.

**Tests use `dependency_overrides`, not real logins.** `tests/conftest.py`'s
`client` fixture overrides `get_current_session` directly
(`app.dependency_overrides[get_current_session] = ...`) to return a static
`SessionContext`, rather than round-tripping through `/auth/login` — every
CRUD test file dropped its `_login()`/`settings` boilerplate as a result.
`set_session_role("member")` swaps the override mid-test to flip roles on
the same client, which is what `tests/test_permissions.py` uses to prove a
`monkeypatch`ed `PERMISSIONS` entry actually 403s a `member` while an
`owner` still succeeds. `raw_client` (no override) is kept for `test_auth.py`,
which is the one place that needs to exercise the real cookie/session flow.

**Still open:** the registry has no real entries — when a product
requirement shows up (e.g. "members can log entries but only owners can
archive a vehicle"), it's one line in `PERMISSIONS`. Reads (list/get) are
intentionally never gated; only the five mutating actions go through
`check_permission()`, since nothing today needs read-level restriction and
adding it would be speculative. `ai-service`/`worker` don't consume
`aria_auth` yet — there's nothing in either service that needs a session
today — but the factory pattern means adopting it later is "call
`build_get_current_session(get_db_dep)` once," not a redesign.

---

## What's already solid (context, not debt)

For contrast — these were built with future entity types in mind and don't
need rework as domain count grows:

- `DOMAIN_REGISTRY` / `ENTITY_DOMAINS` registries (`domains/index.ts`,
  `entities/__init__.py`) — adding a domain is "new config file + 3
  registrations," not a scattered search-and-add.
- `EntityForm.tsx` — fully field-driven off `DomainConfig.fields`, no
  per-domain form code.
- `entities`/`logs`/`schedules` routers and Mongo collections — one generic
  polymorphic collection and router per resource, not one per domain.
- `EntityListPage.tsx` / `EntityDetailPage.tsx` — single generic pages, not
  one per domain; domain filter chips are registry-driven.
- TanStack Query hooks (`useEntities`, `useLogs`, `useSchedules`) — generic
  over `EntityDomain`, no per-domain hook boilerplate.
