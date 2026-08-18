# Scaling-debt #12: No CI/CD pipeline

## Context

`docs/scaling-debt.md` item #12 (🔴, "blocks-AWS-launch in spirit") notes
there's no `.github/` directory anywhere — no automated test run, build, or
deploy gate before a merge lands. Today's deploy is a manual `docker compose
... --build` run by hand. This is the next unaddressed item in the doc's
numbered order (the last fix, #11, landed 2026-08-17).

Research findings that shape this plan:
- The repo is a single `uv` workspace (`libs/shared`, `libs/auth`,
  `services/core-api`, `services/ai-service`, `services/worker`) with one
  root `pyproject.toml`/`uv.lock`. Every Python test suite already runs
  against in-memory fakes (`mongomock-motor`, hand-rolled Redis/Mongo fakes)
  — **no live Mongo/Redis/Chroma needed to run tests in CI**.
- `services/frontend` is a separate npm project (React 19 + Vite 8 +
  TypeScript, `oxlint`, no test framework installed — `npm run build` is
  `tsc -b && vite build`, which doubles as the typecheck gate).
- `.pre-commit-config.yaml` today has exactly one hook: a check that
  `services/frontend/src/domains/generated.ts` is up to date with the
  Pydantic entity models. No ruff/mypy/black exist anywhere in the repo yet.
- `docker/python-service.Dockerfile` (shared by core-api/ai-service/worker)
  installs `tesseract-ocr`, `poppler-utils`, and pango/harfbuzz libs — these
  are **hard import-time requirements**: `core-api/app/routers/entities.py`
  does `from weasyprint import HTML` at module scope, and
  `worker/app/logic/ocr.py` imports `pytesseract`/`pdf2image` at module
  scope. CI must install the same system packages or core-api/worker tests
  will fail to even collect.
- GitHub remote is already configured (`origin` → `github.com/TheRoaringPigeon/ARIA`),
  so GitHub Actions is viable with no extra setup.
- Per the user's scope choices: **add ruff as new lint tooling** (not mypy —
  too large an adoption for this pass) and **include a Docker image build
  gate** (build all 4 deployable images in CI, no push/registry needed since
  deploy stays manual).
- Deploy automation (CD) stays explicitly out of scope — the doc frames this
  as "not a hard technical blocker," and this repo's manual
  `docker compose -f docker-compose.prod.yml up -d --build` deploy step is a
  deliberate single-operator choice, not something this item asks to change.

## Approach

### 1. Write this plan to `docs/scaling-debt-plans/sd13-ci-cd-pipeline.md`
Following the existing convention (`sd1-2...md` through `sd12...md`) — this
is the permanent record the user asked for; the plan-mode file is scratch.

### 2. New workflow: `.github/workflows/ci.yml`
Triggers on `pull_request` and `push` to `main`, with a `concurrency` group
(`cancel-in-progress: true`) keyed on ref so rapid pushes don't pile up. Four
jobs, all independent (no `needs:` chaining — fail-fast per job, not
sequential):

- **`python-tests`** — matrix over the 5 uv workspace packages
  (`aria-shared`, `aria-auth`, `core-api`, `ai-service`, `worker`). Each leg:
  `apt-get install` the same system packages `docker/python-service.Dockerfile`
  installs (unconditionally for every leg, mirroring that Dockerfile's own
  "not worth conditional complexity" reasoning), `astral-sh/setup-uv` with
  caching enabled (`cache-dependency-glob: uv.lock`), then
  `uv sync --frozen --package <name>` and `uv run --package <name> pytest`.
- **`docker-build`** — matrix over the 4 deployable images: core-api,
  ai-service, worker (all via `docker/python-service.Dockerfile` with the
  matching `SERVICE` build arg) and frontend (`services/frontend/Dockerfile`,
  `build` target, with placeholder `VITE_CORE_API_URL`/`VITE_AI_SERVICE_URL`
  build args — only verifying the build succeeds, not runtime values). Uses
  `docker/setup-buildx-action` + `docker/build-push-action` with
  `push: false` and `cache-from/to: type=gha` for layer caching across runs.
- **`frontend`** — `actions/setup-node@v4` (node 22, matching
  `services/frontend/Dockerfile`'s `node:22-slim`), `npm ci`, `npm run lint`
  (oxlint), `npm run build` (tsc typecheck + vite build).
- **`pre-commit`** — `astral-sh/setup-uv`, `uv run pre-commit run
  --all-files` at repo root. This now covers **both** the existing
  generated-ts drift check **and** the new ruff hook (below) in one job, no
  duplicate lint step needed elsewhere.

### 3. Add ruff
- Root `pyproject.toml`: add `ruff>=0.8` to the existing root
  `dependency-groups.dev` (alongside `pre-commit`), and a `[tool.ruff]`
  section — `target-version = "py312"`, no custom `select` (keep ruff's
  built-in default rule set: `E4`, `E7`, `E9`, `F` — low-noise pyflakes +
  basic pycodestyle, the standard safe starting point for first adoption).
  No `ruff format` / line-length changes — this pass is lint-only, not
  reformatting the whole codebase.
- `.pre-commit-config.yaml`: add the `astral-sh/ruff-pre-commit` repo's
  `ruff-check` hook (with `--fix` for local convenience — CI still fails red
  if a fix would have been needed but wasn't committed, which is
  pre-commit's standard behavior).
- **Real implementation work, not just config**: run `uv run ruff check .`
  over the whole workspace once wired up, and fix (or add narrowly-scoped
  `# noqa` / `[tool.ruff.lint.per-file-ignores]` entries with a reason, for
  genuine false positives) whatever it finds, so CI starts green rather than
  landing pre-broken.

### 4. Manual follow-up (not part of this code change)
Once this workflow has run green at least once on `main`, enabling GitHub
branch protection on `main` (require the 4 new status checks before merge)
is a repo Settings action — same "documented manual step, not automated"
treatment as sd6a's production Mongo replica-set migration. Note this in the
`sd13` doc rather than attempting it via API.

## Files touched
- `.github/workflows/ci.yml` (new)
- `pyproject.toml` (root) — add `ruff` to dev group + `[tool.ruff]` config
- `.pre-commit-config.yaml` — add `ruff-check` hook
- Whatever files `ruff check .` flags — fixed or narrowly ignored
- `docs/scaling-debt-plans/sd13-ci-cd-pipeline.md` (new — this plan)
- `docs/scaling-debt.md` — flip item #12 to 🟢 with a summary, once verified

## Verification
- Push the branch and open a PR (or push directly, since CI also runs on
  `push` to `main`) and confirm all 4 jobs go green in the Actions tab.
- Deliberately break something small locally first to confirm each gate
  actually catches its failure mode before relying on it: a failing
  `pytest` assertion in one package, a ruff violation, a Dockerfile typo (bad
  apt package name), a frontend type error — revert each after confirming
  the corresponding job goes red.
- Confirm `uv run pre-commit run --all-files` and `uv run ruff check .` both
  pass locally (dev loop parity with CI).
- Confirm the existing local dev flow (`docker compose up`) still works
  unchanged — this item adds CI, it doesn't touch runtime config.
