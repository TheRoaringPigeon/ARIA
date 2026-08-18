# sd13: CI pipeline (GitHub Actions)

Closes `scaling-debt.md` item [#12](../scaling-debt.md#12--no-cicd-pipeline).

## What shipped

New `.github/workflows/ci.yml`, triggered on every push to `main` and on
pull requests, with a `concurrency` group (`cancel-in-progress: true`) keyed
on ref so rapid pushes don't queue up stale runs. Four independent jobs, no
`needs:` chaining:

- **`python-tests`** — matrix over all 5 `uv` workspace packages
  (`aria-shared`, `aria-auth`, `core-api`, `ai-service`, `worker`). Installs
  the same system packages `docker/python-service.Dockerfile` installs
  (`tesseract-ocr`, `poppler-utils`, `libpango-1.0-0`, `libpangoft2-1.0-0`,
  `libharfbuzz-subset0`, `fonts-liberation`) unconditionally on every leg,
  mirroring that Dockerfile's own "not worth conditional complexity" call —
  `core-api/app/routers/entities.py` imports `weasyprint` and
  `worker/app/logic/ocr.py` imports `pytesseract`/`pdf2image` at module
  scope, so these aren't optional even for legs that don't strictly need
  them. Uses `astral-sh/setup-uv` with `uv.lock`-keyed caching, then
  `uv sync --frozen --package <name>` and `uv run --package <name> pytest`,
  **both run with `working-directory` set to that package's own directory**
  — not repo root. This matters: pytest resolves config by walking up from
  cwd to the nearest `pyproject.toml`. From repo root it finds the root
  `pyproject.toml` (no `testpaths`), discovers every package's tests at
  once, and blows up on duplicate `tests/` module names across packages.
  Scoped to the package directory, it finds that package's own
  `testpaths = ["tests"]` and resolves cleanly.
- **`docker-build`** — matrix over the 4 deployable images: core-api,
  ai-service, worker (`docker/python-service.Dockerfile`, matching
  `SERVICE` build arg) and frontend (`services/frontend/Dockerfile`,
  `build` target, placeholder `VITE_CORE_API_URL`/`VITE_AI_SERVICE_URL`
  build args — verifying the build succeeds, not runtime correctness).
  `docker/setup-buildx-action` + `docker/build-push-action` with
  `push: false` and `cache-from`/`cache-to: type=gha` (scoped per image) for
  cross-run layer caching. Directly closes the "no build gate" half of the
  item — a Dockerfile/`uv.lock` break (e.g. a renamed apt package) now fails
  CI instead of surfacing only when someone runs `docker compose up --build`
  by hand.
- **`frontend`** — `actions/setup-node` (node 22, matching
  `services/frontend/Dockerfile`'s `node:22-slim`), `npm ci`,
  `npm run lint` (oxlint), `npm run build` (`tsc -b && vite build` — doubles
  as the typecheck gate; no test framework exists for the frontend yet, so
  there's no `npm test` step to add).
- **`pre-commit`** — `uv run pre-commit run --all-files` at repo root,
  covering both the existing generated-`domains.ts` drift check and the new
  ruff hook (below) in one job.

## Ruff, added as new tooling (scope confirmed with the user)

No Python lint/type-check tooling existed anywhere in the repo before this.
Per the user's explicit choice, this item adds **ruff** (not mypy — too
large an adoption for one pass) as a new lint gate, not just wiring up CI
for what already existed.

Root `pyproject.toml` gained `ruff>=0.8` in the existing root
`dependency-groups.dev` (alongside `pre-commit`) and:

```toml
[tool.ruff]
target-version = "py312"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]
```

**Deviation from the original plan, found during implementation:** the plan
assumed leaving `select` unset would fall back to ruff's traditional minimal
default (pyflakes + basic pycodestyle). Empirically, ruff 0.16.3's actual
out-of-the-box default (confirmed via `ruff check --isolated --show-settings`,
ignoring all config) enables **415 rules** across many plugin families
(flake8-bugbear, pyupgrade, isort, pylint, flake8-datetimez, ...) — running
that unfiltered against this codebase produced 316 findings, dominated by
150 `B008` hits that are false positives by construction (FastAPI's
`Depends()`-in-a-default-argument pattern, which `flake8-bugbear` doesn't
know is intentional). Adopting all of that in one pass would have meant
either mass-suppressing a huge rule surface or reviewing 316 findings
line-by-line — both disproportionate to this item's scope. `select` is now
pinned explicitly to `["E4", "E7", "E9", "F"]`, the traditionally-minimal
baseline the plan actually intended. Broader rule adoption (bugbear,
pyupgrade, etc.) is a real future decision, not built here.

With that explicit `select`, the whole workspace had exactly **one**
real finding: an unused `ActionResult` import in
`services/ai-service/app/agents/nodes.py` (confirmed genuinely unused, not
a false positive) — fixed via `ruff check --fix`. CI is not landing
pre-broken.

`.pre-commit-config.yaml` gained the `astral-sh/ruff-pre-commit` repo's
`ruff-check` hook (`args: [--fix]`, for local auto-fix convenience — CI still
fails red if a fix was needed but not committed, standard pre-commit
behavior).

## Explicitly out of scope

- **Deploy automation (CD)** — the doc frames deploy as "not a hard
  technical blocker," and this repo's manual
  `docker compose -f docker-compose.prod.yml up -d --build` step is a
  deliberate single-operator choice this item doesn't change.
- **mypy / type-checking** — a much larger adoption surface than ruff's
  lint-only pass; deferred as a separate future decision.
- **`ruff format`** — this pass is lint-only; running a formatter over the
  whole codebase would produce a large, unrelated diff.
- **Branch protection requiring these checks** — a GitHub repo Settings
  action, not something committed in code. Manual follow-up once this
  workflow has run green at least once on `main`: enable "Require status
  checks to pass" for `main` and select the 4 new checks
  (`python-tests`, `docker-build`, `frontend`, `pre-commit`).

## Verified locally (this workflow itself can't run until pushed)

Every command the workflow invokes was exercised for real before landing,
since a fresh GitHub Actions run can't be observed from this environment:

- `uv run ruff check .` (workspace-wide, with the final `select`) →
  1 finding, fixed, then clean. `uv run pre-commit run --all-files` → both
  hooks (`ruff-check`, the generated-ts check) pass.
- `pytest`, run exactly as CI will (cwd inside each package directory):
  `aria-shared` 5 passed, `aria-auth` 22 passed, `ai-service` 227 passed,
  `worker` 50 passed — all directly on this (Windows) host, since none of
  those import weasyprint/tesseract at module scope.
  `core-api` can't be verified natively on Windows this way (weasyprint's
  `from weasyprint import HTML` fails to load `libgobject-2.0-0`, which
  doesn't exist outside Linux) — instead, built the real
  `docker/python-service.Dockerfile` image for `core-api`
  (`docker build -f docker/python-service.Dockerfile --build-arg
  SERVICE=core-api ...`) and ran `uv sync --frozen && uv run pytest` inside
  it, the closest available proxy to the actual Linux CI runner: **259
  passed**, confirming both that the apt package list is sufficient and that
  the `working-directory`-scoped pytest invocation resolves correctly.
- All 4 `docker-build` matrix legs built successfully as standalone
  `docker build`/`docker build -f services/frontend/Dockerfile --target
  build` invocations (core-api, ai-service, worker, frontend) — same
  Dockerfiles/build-args the workflow uses. Throwaway images removed after.
- `npm ci && npm run lint && npm run build` in `services/frontend` — lint
  passed (2 pre-existing `react/only-export-components` warnings, not
  errors, in `ThemeContext.tsx` — unrelated to this change, not fixed here),
  build succeeded.
- `.github/workflows/ci.yml` parses as valid YAML
  (`yaml.safe_load`). `actionlint` wasn't available in this environment to
  do deeper GitHub-Actions-specific validation — the underlying risk is
  low since every shell command the workflow runs was independently
  verified above; only the YAML wiring itself (action versions, matrix
  interpolation) is unverified until the first real run.

**Not yet verified:** an actual GitHub Actions run (needs a push), and the
branch-protection manual follow-up above.
