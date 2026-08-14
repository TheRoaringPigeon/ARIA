# SD10 — Seed household/admin account: make seeding opt-in, not always-on

Tracks a follow-up on [`scaling-debt.md` #9](../scaling-debt.md#9-🟢-seed-householdadmin-account-created-unconditionally-with-a-guessable-default-password).

## Problem

`ensure_seed_household` (`services/core-api/app/seed.py`) runs unconditionally
in `main.py`'s `lifespan`, on every single core-api boot. It's idempotent
(only inserts a user if `owner@household.local` doesn't already exist), so
it's a cheap no-op after the first boot — but for a deployment meant to run
for years, that's still a startup DB round trip and a standing
`admin_password` setting that exist purely to serve one moment in the
deployment's entire lifetime: the very first boot, before any household
exists.

SD9 looked at this exact seam and explicitly scoped it out:

> Not doing: an `ENVIRONMENT`-gated skip of seeding entirely... Both are
> bigger product changes than this item's `minor-cleanup` severity calls
> for.

That was the right call for SD9's narrower goal (stop an insecure default
from reaching prod unnoticed). This item revisits that scope decision
directly, at the user's request: seeding should be opt-in (explicitly turned
on for the one deploy that needs it) rather than opt-out (always on, with a
fail-fast safety net bolted on top).

This does not change what the seed account *is* — per
`docs/plans/ok-let-s-take-a-starry-cat.md`, `owner@household.local` remains
the intended real login for this single-household deployment. It only
changes when the seeding logic actually runs.

## Approach

1. **`app/config.py`** — add `seed_admin: bool = False` (env
   `CORE_API_SEED_ADMIN`), placed just above `admin_password` since it gates
   whether that field is ever consulted. Update
   `_require_real_admin_password_outside_dev` to additionally require
   `self.seed_admin`, so the fail-fast check only fires when seeding will
   actually run:

   ```python
   if self.seed_admin and self.environment != "development" and (
       not self.admin_password or self.admin_password == "aria-dev"
   ):
       raise ValueError(...)
   ```

2. **`app/main.py`** — wrap the existing `ensure_seed_household(get_db())`
   call in `if settings.seed_admin:`.
3. **`docker-compose.yml`** (dev) — add `CORE_API_SEED_ADMIN: "true"` to
   `core-api`, preserving today's zero-config dev startup.
4. **`docker-compose.prod.yml`** — add
   `CORE_API_SEED_ADMIN: ${CORE_API_SEED_ADMIN:-false}`, off by default. An
   operator sets `CORE_API_SEED_ADMIN=true` in `.env.prod` for exactly the
   one `up` that needs to create the first owner login, then removes it.
5. **Tests** — `services/core-api/tests/conftest.py`'s `raw_client` fixture
   monkeypatches `settings.seed_admin = True` before constructing
   `TestClient(app)`, so tests that log in as the seed user
   (`test_auth.py`, `test_auth_signup.py`) keep working against a fresh
   mongomock DB every run. `test_config.py` gains a default-off case and a
   "production with seeding off doesn't require a real password" case,
   alongside the existing (now `seed_admin=True`-scoped) fail-fast cases.

## Scope decision

Not doing: removing the `environment` setting. Its role is unchanged (dev
tolerates the known-weak `"aria-dev"` default; non-dev doesn't) — it's just
now combined with `seed_admin` rather than being the sole gate. Not doing:
any change to what the seed account is or how login works — this only
changes when `ensure_seed_household` executes.

## Verification plan

- Full `core-api` test suite passes, including rewritten/new
  `test_config.py` cases.
- Live, dev compose: `docker compose up` still logs in as
  `owner@household.local` / `aria-dev` out of the box, no extra config.
- Live, simulated prod steady-state: `CORE_API_ENVIRONMENT=production`,
  `CORE_API_SEED_ADMIN` unset, no `CORE_API_ADMIN_PASSWORD` — boots cleanly,
  does not seed.
- Live, simulated prod bootstrap: same plus `CORE_API_SEED_ADMIN=true` —
  fails fast on a missing/default password (unchanged from SD9), seeds
  successfully once a real password is set.
- No live-migration risk: the already-running prod deployment
  (`aria.roaringpigeon.com`) already has its owner account; the next
  redeploy without the flag set simply stops re-running a check that was
  already a no-op.
