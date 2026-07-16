# Scaling Debt

Design decisions that work fine at today's scale (5 entity domains, single
developer, no auth enforcement) but will get more expensive to live with as
the app grows — more entity types, more contributors, more UI variants. This
is a tracking list, not a plan: each item needs its own design decision
before it's fixed. Written 2026-07-15, after the entity-domain-registry
refactor (commit `9f20952`).

Status legend: 🔴 not started · 🟡 partially addressed · 🟢 fixed

---

## 1. 🔴 Entity schema is hand-duplicated across Python and TypeScript

**Where:** `libs/shared/src/aria_shared/models/entities/*.py` (source of
truth per `docs/architecture.md`) vs. `services/frontend/src/domains/*.ts`.
Every domain's attributes, valid statuses, and log types are typed once in
each language:

| Concept | Backend | Frontend |
|---|---|---|
| Domain list | `entities/__init__.py:18` | `domains/index.ts:11-17` |
| Attrs shape | `entities/person.py:7-18` | `domains/person.ts:3-11` |
| Valid statuses | `person.py:9` | `person.ts:24` |
| Log types | `person.py:10` | `person.ts:25` |
| Global `LogType` union | `models/logs.py:9-20` | `api/types.ts:19-29` |

**Why it's fine today:** 5 domains, one person maintaining both sides in the
same PR.

**Why it won't scale:** No codegen, no shared schema, no lint rule or test
that catches drift. At 10+ domains, or with more than one contributor,
frontend and backend will silently diverge — e.g. frontend allows a status
value the backend rejects, or a new log type is added on one side and
forgotten on the other. Errors only surface at request time, as a raw
`ApiError` string.

**Options to evaluate later:** generate the frontend `domains/*.ts` configs
from the Pydantic models at build time (OpenAPI schema export + codegen), or
introduce a single JSON/YAML domain-definition format that both sides
consume. Either is a real design decision, not a quick patch — worth
scoping deliberately.

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

**Where:** `libs/shared/src/aria_shared/models/logs.py:9-20` and
`services/frontend/src/api/types.ts:19-29` — a single `Literal[...]` /
union type listing every log type across every domain.

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
