# ARIA
ARIA — Adaptive Residential Intelligence Assistant

A personal AI-powered home operations platform. See [`docs/data-model.md`](docs/data-model.md)
for the data model.

## Architecture

Microservices, each containerized, orchestrated with `docker-compose`:

| Service | Role | Depends on |
|---|---|---|
| `frontend` | React 18 + TS + Vite + Tailwind + TanStack Query | `core-api`, `ai-service` |
| `core-api` | FastAPI — entities/logs/schedules/documents CRUD. Mongo only, zero AI dependency. | `mongo` |
| `ai-service` | FastAPI — RAG chat, LangGraph agents (not yet implemented). | `chromadb`, `ollama`, `core-api` |
| `worker` | Celery — OCR/chunk/embed pipeline (not yet implemented, `ping` task only). | `redis` |
| `ollama` | Local LLM inference — no external API calls/keys. | — |

All LLM inference runs in-house against the `ollama` container (default model
`qwen3:14b`, overridable via `OLLAMA_MODEL` in `.env`) — there's no external
LLM API dependency or key to configure.

`core-api` is built to keep working standalone if `ai-service`/`worker`/`chromadb`
are down — that's the PRD's "strict decoupling" principle as an actual service
boundary, not just an internal module split.

Domain models live once, in `libs/shared` (`aria-shared`), and are pulled into
each Python service as a `uv` workspace member, resolved and locked together
via the root `uv.lock` — one canonical schema, independently deployable
services.

## Running locally

`ollama` is shared between dev and prod (one GPU, stateless inference — no
reason to run it twice) and lives in its own always-on compose file. Start it
once, then start dev normally:

```
docker compose -p aria-llm -f docker-compose.llm.yml up -d --build
docker compose up --build
```

- Frontend: http://localhost:5173
- core-api: http://localhost:8000 (docs at `/docs`)
- ai-service: http://localhost:8001 (docs at `/docs`)
- Mongo: localhost:27017
- Chroma: http://localhost:8002
- Ollama: http://localhost:11434

The `ollama` container pulls its model on first start (see
`docker/ollama/entry.sh`), so the first `docker-compose.llm.yml up` will take
longer while the model downloads. It requests a GPU (`runtime: nvidia`) by
default — drop the `deploy`/`runtime` lines in `docker-compose.llm.yml` for
CPU-only.

The frontend's landing page hits both services' `/health` endpoints to confirm
the stack is wired up correctly.

## Running prod

Prod is a second, independent stack (`docker-compose.prod.yml`) that can run
alongside dev on the same machine — distinct volumes, no `--reload`/bind
mounts (it runs whatever was baked into the image at build time), and the
frontend is a real production build instead of the Vite dev server. It
shares the same `ollama` container as dev (start `docker-compose.llm.yml`
first, if it isn't already running).

Unlike dev, prod is fronted by [Caddy](https://caddyserver.com/) (see
`./Caddyfile`) on a real domain with automatic HTTPS (Let's Encrypt) — this
is what makes it reachable from a phone, both over plain HTTPS in a browser
and as an installed PWA (service workers require HTTPS). Caddy's 80/443 are
the *only* host-exposed ports; mongo/chromadb/minio/core-api/ai-service/
frontend are all internal-only, reachable from the host for debugging via
`docker compose -p aria-prod ... exec <service> ...` rather than a
published port (same as the backup/restore/migration commands below already
do).

**One-time setup**, before the first `up`:

1. Point a DNS A/AAAA record at this machine's public IP — e.g.
   `aria.yourdomain.com`. Sub-domain, not a path on a shared domain: Caddy
   here fronts one app on one domain, not several apps split by path.
2. Forward `80/tcp` and `443/tcp+udp` on your router to this machine (Caddy
   needs 80 for the ACME HTTP challenge, in addition to 443 for HTTPS
   itself).
3. Only one Caddy-fronted app can hold host ports 80/443 on this machine at
   a time — if you run others here (e.g. a separate project's own Caddy
   container), stop that stack before starting this one, or vice versa.

```
cp .env.prod.example .env.prod   # fill in real secrets, including CADDY_DOMAIN — see comments in the file
docker compose -p aria-prod -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

Prod doesn't auto-reload — after pulling code changes, re-run the same `up -d
--build` command to rebuild and replace the running containers.

### Installing on a phone

Once prod is up and `https://<CADDY_DOMAIN>` loads in a mobile browser:

- **Android (Chrome):** an "Install app" / "Add to Home screen" prompt
  appears automatically; the app opens full-screen from the home screen icon
  from then on.
- **iOS (Safari):** Share → "Add to Home Screen" (Safari doesn't show an
  automatic install prompt the way Chrome does, but the result is the same).

If the install prompt doesn't appear, confirm the page is actually being
served over HTTPS (not a self-signed/expired cert warning) — service workers
refuse to register otherwise.

## Backing up

```
scripts\backup-mongo.ps1 <dev|prod>   # mongodump --archive --gzip -> backups/mongo-<target>-<timestamp>.gz
scripts\backup-minio.ps1 <dev|prod>   # tars the minio container's /data -> backups/minio-<target>-<timestamp>.tar.gz
```

`backups/` is gitignored — a backup sitting on the same disk as prod isn't a
real backup, so copy it off-machine periodically (external drive, cloud
storage) rather than leaving it there. Also run `scripts\backup-mongo.ps1 prod`
immediately before any prod migration (see below), on top of whatever regular
cadence you're already backing up on.

## Restoring

```
scripts\restore-mongo.ps1 <dev|prod> <archive-file>   # mongorestore --drop
scripts\restore-minio.ps1 <dev|prod> <archive-file>   # wipes and re-extracts /data
```

Both are destructive (they drop/wipe the target's existing data first) and
require typing the target name to confirm before proceeding.

## Migrations

Mongo is schemaless, so there's no migration framework here — the existing
convention (see `app/indexes.py`'s `ensure_indexes` and `app/seed.py`'s
`ensure_seed_household`) is small, idempotent, safe-to-rerun functions. Most
additive field changes don't need a migration at all — code should default
missing fields rather than assume every document has been backfilled (see
`tests/test_sharing_pre_migration.py` for the pattern). For changes that do
need one (renames, restructuring, backfills):

1. Add `services/core-api/app/migrations/NNN_description.py` with an
   idempotent `async def run(db): ...` and a `__main__` block that calls it
   against `app.db.get_db()`.
2. Run it against dev and verify:
   ```
   docker compose exec core-api uv run python -m app.migrations.NNN_description
   ```
3. `scripts\backup-mongo.ps1 prod`, then run the same script against prod:
   ```
   docker compose -p aria-prod -f docker-compose.prod.yml --env-file .env.prod exec core-api uv run python -m app.migrations.NNN_description
   ```

## Moving data between dev and prod

No separate tooling — it's the backup/restore scripts above, applied across
targets. E.g. pulling a copy of real prod data down into dev to debug
against:

```
scripts\backup-mongo.ps1 prod
scripts\restore-mongo.ps1 dev backups/mongo-prod-<timestamp>.gz
scripts\backup-minio.ps1 prod
scripts\restore-minio.ps1 dev backups/minio-prod-<timestamp>.tar.gz
```

Never run this the other direction (restoring dev data into prod) except
deliberately — it overwrites real household data with test data.

## Repo layout

```
pyproject.toml      root uv workspace definition ([tool.uv.workspace]) — no installable root package
uv.lock              single lockfile for the whole workspace (all 3 services + aria-shared)
libs/shared/       aria-shared — Pydantic domain models, used by all Python services
services/
  frontend/
  core-api/
  ai-service/
  worker/
docs/               design docs (data model, etc.)
local/              gitignored — personal/working files, not part of the app
```
