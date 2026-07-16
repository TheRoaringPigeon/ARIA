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

## 4. 🔴 Global flat `LogType` union, not scoped per domain

**Where:** `libs/shared/src/aria_shared/models/logs.py:9-20` (the Python
`Literal[...]`) and `services/frontend/src/domains/generated.ts`'s
`LOG_TYPES`/`LogType` (generated from it — see #1; `api/types.ts` now just
re-exports `LogType` from `domains`) — a single flat union listing every log
type across every domain.

**Why it's fine today:** Small enough list, only checked at runtime via
`ENTITY_DOMAINS[domain].LOG_TYPES` (`logs.py` validator), which does work
correctly today.

**Why it won't scale:** The union grows unbounded as domains are added, and
nothing at the type level stops a `home` log from using a `person`-only log
type — it's only caught by the runtime validator (`_check_type_valid_for_domain`),
not by TypeScript or Pydantic's static types. More domains means more
opportunity for a wrong-domain log type to slip past code review and only
get caught (or not) at request-validation time.

**Fix shape:** worth revisiting once domain count grows — e.g. discriminated
per-domain log-type literals, mirroring how `EntityAttributes` is already
discriminated by domain.

---

## 5. 🔴 No permission/role enforcement seam

**Where:** `User.role` (`libs/shared/.../models/household.py`, `"owner"` |
`"member"`) exists in the model but `core-api`'s `dependencies.py:22-44`
only checks session validity + `household_id` scoping — role is never read
or enforced anywhere.

**Why it's fine today:** Single-household, trusted-user use case — no
product requirement yet for member-vs-owner restrictions.

**Why it won't scale:** If any future requirement needs per-role or
per-domain permissions (e.g. "members can log entries but only owners can
archive a vehicle"), there's currently no policy layer to hook into — it
would have to be threaded into every router by hand, one at a time, since
nothing mirrors the domain registry for permissions.

**Fix shape:** not urgent; flagged so it's a deliberate design decision
(e.g. a policy/permission registry keyed by domain + action) rather than
retrofitted piecemeal under time pressure later.

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
