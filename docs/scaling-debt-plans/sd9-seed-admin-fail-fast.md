# SD9 — Seed household/admin account: fail fast on a missing/default prod password

Tracks [`scaling-debt.md` #9](../scaling-debt.md#9-🔴-seed-householdadmin-account-created-unconditionally-with-a-guessable-default-password).

## Problem

`ensure_seed_household` (`services/core-api/app/seed.py`) runs unconditionally
in `main.py`'s `lifespan`, seeding `owner@household.local` with
`settings.admin_password` — which defaults to the hardcoded `"aria-dev"`
(`app/config.py:25`) unless `CORE_API_ADMIN_PASSWORD` is set.

`docker-compose.prod.yml` does wire a real value today
(`CORE_API_ADMIN_PASSWORD: ${CORE_API_ADMIN_PASSWORD}`, sourced from
`.env.prod`), and per `docs/plans/ok-let-s-take-a-starry-cat.md`, this seeded
account is the intended real login for this single-household deployment, not
just a dev convenience — so the fix is not "stop seeding in prod," it's
"never let prod boot with the insecure default in place." Two ways that can
happen with today's code, neither of which is caught:

1. A future deploy path (ECS task def, ad-hoc redeploy) simply omits the var
   — `admin_password` silently falls back to `"aria-dev"`.
2. `.env.prod.example` ships `CORE_API_ADMIN_PASSWORD=` with an intentionally
   blank value for the operator to fill in — if they forget,
   `.env.prod` provides an actual empty string, not "unset," so
   pydantic-settings never falls back to the `"aria-dev"` default. This case
   wouldn't be caught by a check that only compares against the literal
   default.

Either way, the seeded owner account goes live with a guessable/empty
credential and nothing signals it.

## Approach

Add an explicit environment signal, and validate against it at settings
construction time (i.e. at process boot, before `lifespan` ever runs) —
failing fast with a crash-on-boot beats "refuse to seed" because a silently
skipped seed just means no login exists yet with no explanation either.

1. **`app/config.py`** — add `environment: str = "development"`
   (env `CORE_API_ENVIRONMENT`), following the same
   default-safe-for-dev/explicit-in-prod shape already established by
   `cookie_secure`. Add a `model_validator(mode="after")` that raises
   `ValueError` when `environment != "development"` and `admin_password` is
   either falsy (empty string) or still equal to the literal `"aria-dev"`
   default — covering both gaps above in one check.
2. **`docker-compose.prod.yml`** — add `CORE_API_ENVIRONMENT: production` to
   the `core-api` service's `environment` block, alongside the existing
   `CORE_API_COOKIE_SECURE: "true"`. Hardcoded in the compose file, not
   sourced from `.env.prod` — it's not a secret, it's which compose file is
   in play, same reasoning as `cookie_secure`.
3. **`docker-compose.yml`** (dev) — no change. `environment` stays defaulted
   to `"development"`, so the validator never fires and today's zero-config
   dev startup (no `CORE_API_ADMIN_PASSWORD` needed) is unaffected.
4. **Tests** — new `services/core-api/tests/test_config.py`:
   - `environment="development"` (default) with the default `admin_password`
     constructs cleanly (today's dev/test behavior, unchanged).
   - `environment="production"` with `admin_password` left at `"aria-dev"`
     raises at `Settings()` construction.
   - `environment="production"` with `admin_password=""` raises.
   - `environment="production"` with a real, non-default `admin_password`
     constructs cleanly.

## Scope decision

Not doing: an `ENVIRONMENT`-gated skip of seeding entirely, or moving the
owner account to a real `POST /auth/signup` flow for prod. Both are bigger
product changes than this item's `minor-cleanup` severity calls for, and the
existing design (seed account *is* the real prod login for a
single-household deployment) is intentional per `ok-let-s-take-a-starry-cat.md`
— this fix only closes the "insecure value reaches production unnoticed"
gap, it doesn't change what the seed does.

## Verification plan

- New `test_config.py` cases pass; full `core-api` suite still passes.
- Live check: run core-api with `CORE_API_ENVIRONMENT=production` and no
  `CORE_API_ADMIN_PASSWORD` set (simulating the gap) and confirm the
  container fails to start with a clear error, instead of booting seeded
  with `"aria-dev"`. Then confirm it boots normally with a real password set.
