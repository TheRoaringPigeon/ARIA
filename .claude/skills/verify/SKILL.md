---
name: verify
description: How to run ARIA end-to-end via docker-compose and drive core-api/worker flows for verification.
---

# Verifying ARIA changes live

## Bring the stack up

`docker compose ps` first — it's usually already running from a prior session.
If not: `docker compose up -d mongo minio redis chromadb core-api worker`
(skip `ollama`/`ai-service`/`frontend` unless the change touches AI chat —
`ollama` needs a GPU and is slow to start).

`core-api` and `worker` mount source via volumes and hot-reload
(`uvicorn --reload`, `watchmedo auto-restart` + celery), so **editing files
under `services/{core-api,worker}/app` or `libs/*/src` takes effect without
a rebuild**. Confirm the reload happened cleanly:

```bash
docker compose logs core-api --tail 30   # look for "WatchFiles detected changes... Reloading" then "Application startup complete"
docker compose logs worker --tail 30     # look for the task list re-registering under [tasks], then "celery@... ready."
```

## Auth

Session-cookie auth, single hardcoded household. Log in once, reuse the
cookie jar:

```bash
curl -s -c /tmp/aria_cookies.txt -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" -d '{"password":"aria-dev"}'
# -> {"household_id":...,"user_id":...,"role":"owner"}
```

(`aria-dev` is `config.py`'s `admin_password` default — check
`CORE_API_ADMIN_PASSWORD` env if it's been overridden.)

Then pass `-b /tmp/aria_cookies.txt` on every subsequent request.

## Gotcha: curl -F file upload with `;type=...`

On this machine's curl (mingw64 8.14.1), `-F "file=@/path;type=application/pdf"`
combined with other `-F` fields intermittently fails with
`curl: (26) Failed to open/read local data from file/application` and no
verbose output. Drop the `;type=...` suffix — FastAPI/`python-multipart`
infers content-type fine without it:

```bash
curl -s -b /tmp/aria_cookies.txt -X POST http://localhost:8000/documents \
  -F "file=@/tmp/sole.pdf" -F "document_type=manual" -F "entity_ids=$ENTITY_ID"
```

Multiple `entity_ids` values (many-to-many upload) are repeated `-F` flags:
`-F "entity_ids=$A" -F "entity_ids=$B"`.

## Driving core-api + worker together

Uploads enqueue `process_document` (OCR/chunk/embed); entity-delete cascade
and document-delete both enqueue `delete_document` (Chroma + S3 + Mongo
cleanup). Both are fire-and-forget from core-api's side — to observe them
actually running, tail the worker:

```bash
docker compose logs worker --tail 20
```

A real Chroma HTTP call and `Task ... succeeded` line confirms the task ran
(and both are safe no-ops if the target's already gone, so this works even
after data was already cleaned up by an earlier step).

To inspect MinIO directly (bypassing core-api) for a household's objects:

```bash
docker run --rm --network aria_default \
  -e AWS_ACCESS_KEY_ID=aria -e AWS_SECRET_ACCESS_KEY=aria-dev-secret \
  amazon/aws-cli --endpoint-url http://minio:9000 \
  s3 ls s3://aria-documents/<household_id>/ --recursive
```

(`aria_default` is the compose-generated network name — confirm with
`docker network ls` if it's ever different.)

## Gotcha: frontend node_modules is an anonymous Docker volume

`docker-compose.yml`'s `frontend` service bind-mounts the source dir but
masks `node_modules` with an anonymous volume (`/app/node_modules`) so the
container's own `npm install` (baked in at build time) isn't clobbered by
the host bind mount. That means adding/upgrading an npm package
(`npm install <pkg>` on the host, which only updates `package.json`/
`package-lock.json`/the host's own `node_modules`) requires **both** a
rebuild **and** forcing the anonymous volume to renew — `docker compose
build frontend` alone is not enough, because `docker compose up -d
frontend` reuses the existing anonymous volume (still holding the old
`node_modules`) rather than the one baked into the freshly built image:

```bash
docker compose build frontend
docker compose up -d --force-recreate -V frontend   # -V renews anonymous volumes
```

Confirm the new package actually landed inside the container (not just in
the host's `package.json`, which the bind mount shows either way):

```bash
docker exec aria-frontend-1 sh -c "ls node_modules | grep <pkg>"
```

## Cleanup

Verification runs create real entities/documents in the dev Mongo/MinIO —
delete them via the API afterward (`DELETE /entities/{id}`,
`DELETE /documents/{id}`) rather than leaving them, since this dev stack's
data persists across sessions in named volumes.
