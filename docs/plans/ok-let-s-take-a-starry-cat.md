# M1 — Core CRUD Tracking: Implementation Plan

## Context

ARIA is currently pure scaffolding (M0, done): four services proven to boot
and talk to each other, one real endpoint (`GET /entities`), no auth, no
writes, no feature UI. M1 is the foundation milestone — per
`docs/roadmap.md`, nothing AI-related should start before this lands, and
the exit bar is concrete: *someone can add a vehicle, log an oil change
against it, set a recurring schedule, and see it show up as "due" later —
entirely through the UI, no AI service involved.*

This plan turns that into working `core-api` write endpoints, real
session-derived auth, the schedule due-date recompute logic
(`data-model.md` §5), and the first real `frontend` feature UI. It's
greenfield on the frontend (only a health dashboard exists) and additive on
the backend (one router exists today: `entities.py`, read-only).

Two scope decisions were confirmed with the user:
- **Auth**: build a real login page (shared password, env-configured),
  backed by a genuine session cookie + seeded household/user documents —
  not a silent auto-login. Session issuance is factored behind a
  `create_session()` helper specifically so a future swap to Keycloak/OIDC
  only touches the login route, not anything downstream (the frontend's
  `useSession()` hook, the route guard, or any protected endpoint's
  `Depends(get_current_session)` never see the password — they only see a
  validated session).
- **Testing**: introduce minimal automated tests now (pytest + httpx +
  mongomock-motor in `core-api`), since the schedule recompute logic is the
  one piece of real business logic in this milestone and is cheap to cover
  with a pure-function unit test. No frontend test framework yet.

---

## Key design decisions

**Auth mechanism** — server-side session, Mongo-backed, cookie-carried.
- New `sessions` collection: `{_id: token, user_id, household_id, created_at, expires_at}`.
- On startup, `core-api`'s FastAPI `lifespan` idempotently upserts one seed
  `Household` + one owner `User` (fixed email, e.g. `owner@household.local`).
- `POST /auth/login` — body `{password: str}`, compared via
  `hmac.compare_digest` against `settings.admin_password`
  (env `CORE_API_ADMIN_PASSWORD`, default `"aria-dev"`). On success, calls a
  shared `create_session(db, user_id, household_id) -> token` helper
  (`secrets.token_urlsafe(32)`, inserted into `sessions`), sets an
  `httpOnly`, `samesite=lax` cookie `aria_session`. **This helper is the
  OAuth swap seam** — a future `GET /auth/callback` for Keycloak validates
  the ID token and calls the same `create_session()`, nothing else changes.
- `GET /auth/me` — returns `{household_id, user_id, user_name}` or 401.
- `POST /auth/logout` — deletes the session doc, clears the cookie.
- `aria_shared/middleware.py`'s `add_permissive_cors` needs
  `allow_origins`/`allow_credentials` params (cookies require a specific
  origin, not `*`). `core-api` calls it with
  `allow_origins=[settings.frontend_origin]` (new config field, default
  `http://localhost:5173`) and `allow_credentials=True`; `ai-service` keeps
  today's `["*"]`/`False` defaults.

**FastAPI DI** — introduce `Depends()` now (not used by the existing
`entities.py`). New `app/dependencies.py`: `get_db_dep()` and
`get_current_session(request, db=Depends(get_db_dep)) -> SessionContext`
(raises 401 on missing/expired cookie). Every new/modified router depends
on this instead of a `household_id` query param — including migrating the
existing `GET /entities`.

**Entities** — full CRUD, archive-in-place (no hard delete, consistent with
the existing `archived_at` field):
- `GET /entities` (session-scoped, optional `domain` filter, excludes archived by default)
- `GET /entities/{id}`
- `POST /entities`
- `PATCH /entities/{id}` (partial update; rejects `archived_at`/`household_id`/`domain` in body)
- `POST /entities/{id}/archive`, `POST /entities/{id}/restore`

**Logs** — create + read only for M1:
- `POST /logs`
- `GET /entities/{entity_id}/logs`
- PATCH/DELETE explicitly deferred — exit criteria only needs create, and
  editing/deleting a schedule-linked log has no reliable rollback path
  (nothing scans historical logs, per `data-model.md` §5). Note the
  deferral in a code comment so it reads as intentional, not an oversight.

**Schedules**:
- `POST /schedules` (includes an initial due-date seed — see algorithm below)
- `PATCH /schedules/{id}` (interval/title/active edits; re-triggers recompute from baseline)
- `GET /entities/{entity_id}/schedules`
- `GET /schedules/due-soon`
- No separate deactivate endpoint — `PATCH {active: false}` covers it.

**Schedule recompute — inline and synchronous**, in the `POST /logs`
handler. Not wrapped in a Mongo transaction (compose's `mongo:7` isn't
running as a replica set, so multi-doc ACID isn't available without extra
infra not justified here): validate everything (schedule exists, belongs to
the same household/entity, usage metric present if required) *before* any
write, then insert the log, then update the schedule. Worst case on a
mid-sequence failure is a stale schedule cache — recoverable, low blast
radius, and explicitly acceptable per `data-model.md`'s own "logging
without a schedule link is still fully valid." Document this tradeoff in
code; flag replica-set init as a possible fast-follow, not required now.

**Two modeling gaps to close in `aria-shared`** (surfaced during
exploration — nothing currently enforces these):
- `EntityBase` gains a `model_validator(mode="after")` checking `status`
  against a `STATUS_BY_DOMAIN` constant (transcribed from `data-model.md`
  §3's per-domain status vocab) and that `self.domain == self.attributes.domain`.
- `Schedule` gains a `model_validator(mode="after")` enforcing:
  `interval_type=="time"` requires `interval_days` and forbids
  `usage_metric`/`interval_usage_amount`; `interval_type=="usage"` requires
  both `usage_metric` and `interval_usage_amount` and forbids `interval_days`.

---

## Recompute algorithm (exact)

Pure function, `services/core-api/app/logic/schedules.py::compute_next_due`
(no Mongo/async — easy to unit test):

- **At schedule creation**: not literally in `data-model.md`'s text, but
  necessary — otherwise a brand-new schedule has no `next_due_at` until a
  log completes it, which breaks "set a schedule, see it due later" without
  first requiring a linked log. `ScheduleCreate` accepts an optional
  `starting_at: date | None` (default: today, time-based) or
  `starting_usage_value: float | None` (usage-based). At creation:
  time → `next_due_at = starting_at + timedelta(days=interval_days)`;
  usage → `next_due_usage_value = starting_usage_value + interval_usage_amount`
  (only if `starting_usage_value` given, else left `None` until first completion).
- **On `POST /logs` with `schedule_id` set**: fetch + validate the schedule
  (household/entity match, active). If `interval_type=="usage"`, require
  `metrics[schedule.usage_metric]` present and float-parseable — reject
  with 400 otherwise. Update `last_completed_log_id`, `last_completed_at`
  (from `log.occurred_at`), and (usage only) `last_completed_usage_value`.
  Recompute: time → `next_due_at = last_completed_at + timedelta(days=interval_days)`;
  usage → `next_due_usage_value = last_completed_usage_value + interval_usage_amount`.
- **On `PATCH /schedules/{id}`** changing interval fields: re-run the same
  function against the existing baseline
  (`last_completed_at ?? starting_at`, `last_completed_usage_value ?? starting_usage_value`)
  so cached due values never go stale relative to the rule.
- **`GET /schedules/due-soon`**: for M1, `is_due` is computed only for
  `interval_type=="time"` (`next_due_at <= today + within_days`, default 30,
  always includes overdue). Usage-based schedules are still returned (so
  the UI can show them) but without a true due/not-due signal — there's no
  reliable "current reading" source yet (the free-text `logs.metrics[usage_metric]`
  key isn't guaranteed to line up with e.g. `VehicleAttrs.current_mileage`).
  Flagged as deferred, not faked. The exit-criteria demo (oil change,
  recurring schedule) uses a time-based interval, fully supported end to end.

---

## File-by-file plan

### `libs/shared/src/aria_shared/`
- `models/entities.py` — add `STATUS_BY_DOMAIN` constant + `model_validator` on `EntityBase`.
- `models/schedules.py` — add `model_validator` on `Schedule`.
- `middleware.py` — extend `add_permissive_cors(app, allow_origins=["*"], allow_credentials=False)`.
- `logs.py`, `household.py`, `types.py` — unchanged.

### `services/core-api/app/`
- `config.py` — add `frontend_origin`, `session_ttl_hours`, `admin_password`, seed household/user constants.
- `dependencies.py` (new) — `get_db_dep`, `SessionContext`, `get_current_session`.
- `auth.py` or `services/session.py` (new) — `create_session()` helper (the OAuth swap seam).
- `seed.py` (new) — `ensure_seed_household(db)`, called from `main.py`'s `lifespan`.
- `routers/auth.py` (new) — `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.
- `routers/entities.py` (modified) — session-derived scoping on `GET /entities`; new single-entity GET, POST, PATCH, archive/restore.
- `routers/logs.py` (new) — `POST /logs`, `GET /entities/{entity_id}/logs`.
- `routers/schedules.py` (new) — `POST /schedules`, `PATCH /schedules/{id}`, `GET /entities/{entity_id}/schedules`, `GET /schedules/due-soon`.
- `schemas/{entities,logs,schedules}.py` (new) — request/response DTOs, kept local to `core-api` (API-contract concern, not canonical stored shape).
- `logic/schedules.py` (new) — `compute_next_due(...)` pure function.
- `main.py` — wire `auth`/`logs`/`schedules` routers; add `lifespan` calling `ensure_seed_household`.
- `pyproject.toml` — add `pytest`, `httpx`, `mongomock-motor` as dev dependencies.
- `tests/` (new) — `test_schedule_recompute.py` (pure-function unit tests), `test_logs_recompute_integration.py`, `test_auth.py`, `test_entities_archive.py`.

### `services/frontend/src/`
- `package.json` — add `react-router-dom` (pairs with the existing TanStack Query; TanStack Router's typed-loader model is more machinery than ~5 routes need).
- `api/client.ts` — `apiFetch` wrapper (`credentials: 'include'`, JSON, error parsing).
- `api/{auth,entities,logs,schedules}.ts` — typed functions + hand-maintained TS types mirroring the Pydantic shapes.
- `hooks/{useSession,useEntities,useEntity,useEntityMutations,useEntityLogs,useCreateLog,useEntitySchedules,useScheduleMutations,useDueSchedules}.ts`.
- `pages/{LoginPage,EntityListPage,EntityDetailPage,DueSoonPage,HealthPage}.tsx`.
- `components/{Layout,EntityForm,LogForm,ScheduleForm,StatusBadge}.tsx` — `EntityForm` is the most complex piece: domain-aware, switching rendered fields per selected `domain`.
- `App.tsx` — rewritten as a router shell with an auth guard (redirect to `/login` if `useSession` 401s); existing health dashboard demoted to a `/health` route rather than deleted.

---

## Sequencing

1. `libs/shared` model/validator changes (small, nothing depends on them yet to break).
2. `core-api` auth infra: config, CORS extension, `dependencies.py`, `create_session()` helper, `seed.py`, `routers/auth.py`. Verify via `/docs` + curl.
3. `core-api` entities CRUD (auth-gated) — verify manually.
4. `core-api` schedules `POST`/`PATCH` (includes initial-seed logic) — verify manually.
5. `core-api` logs `POST` (recompute logic) + `GET .../logs` — verify manually, including the usage-metric-missing 400 case.
6. `core-api` `GET .../schedules` and `GET /schedules/due-soon` (depend on schedules + entities existing).
7. `core-api` tests — `compute_next_due` unit tests as soon as step 4 lands; integration tests alongside steps 5–6.
8. Frontend: API client + `useSession` + `LoginPage` first (unblocks everything behind the auth guard).
9. Frontend: `EntityListPage` + `EntityForm` (create/edit/archive) — lets someone "add a vehicle."
10. Frontend: `EntityDetailPage` with a Logs tab (`LogForm`) — lets someone "log an oil change."
11. Frontend: Schedules tab on `EntityDetailPage` (`ScheduleForm`) — "set a recurring schedule."
12. Frontend: `DueSoonPage` last — ties the exit-criteria walkthrough together.

---

## Verification

**Manual UI walkthrough** matching the exit criteria exactly: log in →
create a vehicle entity → open its detail page → add a log entry
(`type=service`, title "Oil change", `occurred_at`=today) → create a
schedule (`title="Oil change"`, `interval_type="time"`,
`interval_days=90`) → confirm it doesn't show as due at `within_days=30` →
verify it does appear at a longer window or shorter interval → visit
`/due-soon` and confirm the vehicle shows up with the correct
`next_due_at` → archive the vehicle and confirm it drops out of default
entity list/logs/schedule views. Also confirm `docker compose up --build`
still boots clean and `core-api /health` still passes with the AI stack
down (strict decoupling holds).

**Automated**: `uv run pytest` in `core-api` (recompute unit tests + the
handful of integration tests); `npm run lint` (oxlint) and `npm run build`
(`tsc -b && vite build`) in `frontend`.

### Critical files
- `libs/shared/src/aria_shared/models/schedules.py`
- `libs/shared/src/aria_shared/models/entities.py`
- `services/core-api/app/routers/entities.py`
- `services/core-api/app/main.py`
- `libs/shared/src/aria_shared/middleware.py`
- `services/frontend/src/App.tsx`
- `docker-compose.yml`
