# Operations

Runbook for the self-hosted deployment: prod stack, phone install, backups, restores, migrations, and moving data between dev and prod. For local development, see the [README](../README.md#quick-start).

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

Seeding the first owner login (`owner@household.local`) is opt-in
(`app/config.py`'s `seed_admin`) — for this very first `up` only, set
`CORE_API_SEED_ADMIN=true` in `.env.prod` alongside a real
`CORE_API_ADMIN_PASSWORD`. Remove it (or set it back to `false`) afterward —
every later `up`/redeploy should leave it off, since the account already
exists.

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

