# Seed-admin: make seeding opt-in, not always-on

## Context

`ensure_seed_household` (`services/core-api/app/seed.py`) runs unconditionally
in `main.py`'s `lifespan`, on every single core-api boot, forever. It's
idempotent (only inserts if `owner@household.local` doesn't already exist),
so today it's a cheap no-op after the first boot — but for a deployment meant
to run for years, that's still a startup DB round trip and a standing
`admin_password` setting that exist purely to serve one moment in the
deployment's entire lifetime: the very first boot, before any household
exists.

`docs/scaling-debt-plans/sd9-seed-admin-fail-fast.md` already looked at this
exact seam and explicitly scoped out gating seeding behind environment
("Not doing: an `ENVIRONMENT`-gated skip of seeding entirely... bigger
product change than this item's `minor-cleanup` severity calls for"). That
was the right call at the time — the ask then was narrowly "stop an insecure
default from reaching prod unnoticed," which `environment` +
`admin_password` fail-fast validation solved. This plan revisits that scope
decision directly, at the user's request: seeding should be **opt-in**
(explicitly turned on for the one deploy that needs it) rather than
**opt-out** (always on, with a fail-fast safety net bolted on top).

This does not change what the seed account *is* — per
`docs/plans/ok-let-s-take-a-starry-cat.md`, `owner@household.local` remains
the intended real login for this single-household deployment. It only
changes when the seeding logic actually runs.

## Design

Add a new setting, `seed_admin: bool = False` (env `CORE_API_SEED_ADMIN`),
that gates the `ensure_seed_household()` call in `lifespan`. Off by default
everywhere; each compose file decides its own default explicitly:

- **Dev** (`docker-compose.yml`) sets `CORE_API_SEED_ADMIN: "true"` on
  `core-api` — today's "always something to log into out of the box"
  convenience is preserved unchanged for local development and (via the same
  `TestClient(app)` → `lifespan` path) the test suite.
- **Prod** (`docker-compose.prod.yml`) leaves it off by default
  (`${CORE_API_SEED_ADMIN:-false}`) — the steady-state behavior for the rest
  of the deployment's life. An operator sets `CORE_API_SEED_ADMIN=true` in
  `.env.prod` (or an inline override) for exactly the one `up` that needs to
  create the first owner login, then removes it again.

**The existing fail-fast validator gets scoped to only matter when seeding
will actually happen**, instead of firing on `environment` alone:

```python
if self.seed_admin and self.environment != "development" and (
    not self.admin_password or self.admin_password == "aria-dev"
):
    raise ValueError(...)
```

This keeps `environment`'s existing role (dev tolerates the known-weak
`"aria-dev"` default; non-dev doesn't) but adds the `seed_admin` gate on top
— so once the bootstrap deploy is done and the flag is turned back off,
`CORE_API_ADMIN_PASSWORD` is no longer even consulted at boot, matching the
"only needed once" framing. Since `ensure_seed_household` was already
idempotent and the already-running prod deployment (`aria.roaringpigeon.com`)
already has its owner account, this ships with zero live-migration risk —
the next prod redeploy simply stops re-running a check that was already a
no-op.

## Files to change

1. **`services/core-api/app/config.py`**
   - Add `seed_admin: bool = False` (placed just above `admin_password`,
     since it gates whether that field is ever consulted).
   - Update `_require_real_admin_password_outside_dev`'s condition to also
     require `self.seed_admin`, per the snippet above. Update its docstring/
     comment to explain the new scoping.
   - Update `admin_password`'s comment to note it's only meaningful when
     `seed_admin` is true.

2. **`services/core-api/app/main.py`** — wrap the existing
   `await ensure_seed_household(get_db())` call in `if settings.seed_admin:`.

3. **`docker-compose.yml`** (dev) — add `CORE_API_SEED_ADMIN: "true"` to
   `core-api`'s `environment` block, with a short comment pointing at
   `app/config.py`'s `seed_admin`.

4. **`docker-compose.prod.yml`** — add
   `CORE_API_SEED_ADMIN: ${CORE_API_SEED_ADMIN:-false}` to `core-api`'s
   `environment` block, alongside the existing `CORE_API_ADMIN_PASSWORD`/
   `CORE_API_ENVIRONMENT` lines, with a comment explaining the one-time
   bootstrap flow.

5. **`.env.prod.example`** — add a commented `CORE_API_SEED_ADMIN=` line
   explaining it only needs to be set to `true` for the very first `up`
   (creating the owner login), then removed/left unset; update the existing
   `CORE_API_ADMIN_PASSWORD` comment to note it's only consulted while
   `CORE_API_SEED_ADMIN=true`.

6. **`services/core-api/tests/conftest.py`** — the `raw_client` fixture
   currently relies on `ensure_seed_household` running unconditionally via
   `TestClient(app)`'s real `lifespan`. Add `monkeypatch` as a fixture
   dependency and `monkeypatch.setattr(settings, "seed_admin", True)` before
   constructing the `TestClient`, so the seed household still exists for the
   handful of tests that log in as it (`test_auth.py`,
   `test_auth_signup.py::test_signup_is_a_distinct_household_from_the_seed`).
   Everything else (`test_invites.py`, `test_sharing.py`,
   `test_entities_search.py`, `test_users.py`) creates its own household via
   `/auth/signup` and is unaffected either way — confirmed by checking each
   file's usage.

7. **`services/core-api/tests/test_config.py`** — rewrite the four existing
   cases to include `seed_admin=True` where they currently expect the
   validator to fire (it now requires all three conditions), and add:
   - `seed_admin` defaults to `False`.
   - `environment="production"`, `seed_admin=False`, blank/default
     `admin_password` — constructs cleanly (no seeding means no need for a
     real password).

8. **`docs/scaling-debt.md`** — append a short "Follow-up" note under item 9
   recording that seeding is now opt-in via `seed_admin`
   (`docs/scaling-debt-plans/sd10-seed-admin-opt-in.md`), superseding that
   item's original "not doing this" scope call.

9. **`docs/scaling-debt-plans/sd10-seed-admin-opt-in.md`** (new) — short plan
   doc in the same format as `sd9-seed-admin-fail-fast.md`: problem, approach,
   explicit note that this reopens sd9's declined scope, verification plan.

10. **`README.md`**'s "Running prod" one-time-setup steps — add a step noting
    `CORE_API_SEED_ADMIN=true` must be set in `.env.prod` for the very first
    `up` (to create the owner login), and should be removed/set back to
    `false` afterward.

## Verification

- `uv run pytest` in `services/core-api` — full suite passes unchanged
  (seeding still happens in tests via the `raw_client` fixture's monkeypatch;
  new/rewritten `test_config.py` cases cover the new gating logic).
- Live, via the dev compose stack (`docker compose up`): confirm
  `owner@household.local` / `aria-dev` still logs in out of the box with no
  extra config — dev's `CORE_API_SEED_ADMIN: "true"` preserves today's
  behavior.
- Live, simulating prod steady-state: run core-api with
  `CORE_API_ENVIRONMENT=production`, `CORE_API_SEED_ADMIN` unset (defaults
  `false`), and no `CORE_API_ADMIN_PASSWORD` set — confirm it boots
  successfully (no fail-fast) and does not seed anything.
- Live, simulating prod bootstrap: same as above but
  `CORE_API_SEED_ADMIN=true` — confirm it fails fast on a missing/default
  password (unchanged from today), and boots + seeds successfully once a
  real password is set.
