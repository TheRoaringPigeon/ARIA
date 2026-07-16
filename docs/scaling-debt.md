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

## 2. 🔴 `domain === 'person'` branches leak outside the domain registry

**Where:**
- `services/frontend/src/pages/EntityDetailPage.tsx:105` — tab label ("Plans" vs "Schedules")
- `EntityDetailPage.tsx:197, 327` — which form renders (`PlanForm` vs `ScheduleForm`)
- `services/frontend/src/components/LogForm.tsx:28-29` — whether to show cost/metrics fields
- `LogForm.tsx:103` — placeholder text

**Why it's fine today:** Only two "shapes" of domain exist — maintenance-style
(vehicle/equipment/home/project) and relationship-style (person) — so a
single boolean covers it.

**Why it won't scale:** `DomainConfig` (`domains/base.ts:11-19`) — the
registry object the recent refactor built specifically so new domains don't
require touching shared UI code — has no field for this behavior. A future
domain that also wants Plans-style UI (e.g. a `pet` or `subscription`
domain) requires adding `|| domain === 'newthing'` at all five call sites
instead of registering one flag. This re-introduces exactly the kind of
per-type conditional the registry refactor (commit `9f20952`) was meant to
eliminate — it just wasn't caught because it predates that refactor.

**Fix shape:** add a flag to `DomainConfig` (e.g. `uiVariant: 'schedule' |
'plan'` or `usesPlansUI: boolean`) and have the five call sites read
`DOMAIN_REGISTRY[domain]` instead of comparing the domain string directly.

---

## 3. 🔴 `PlanForm.tsx` / `ScheduleForm.tsx` are near-duplicate components

**Where:** `services/frontend/src/components/PlanForm.tsx` (207 lines) and
`ScheduleForm.tsx` (139 lines) — both drive the same underlying `Schedule`
resource, differing only in which `interval_type`s they expose and their
labels ("plan" vs "schedule"). Selected by the same hardcoded check as
item #2.

**Why it's fine today:** Two forms, two domains-shapes, not much
duplicated logic yet.

**Why it won't scale:** Any change to shared `Schedule` behavior (new
field, new validation, a bug fix) has to be made twice and kept in sync by
hand. A third UI variant means a third near-duplicate form.

**Fix shape:** likely resolves naturally alongside #2 — one configurable
form component driven by the registry flag, rather than two hand-forked
components.

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
