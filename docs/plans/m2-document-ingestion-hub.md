# M2 — Document Ingestion Hub: Implementation Plan

## Context

M1 is done (`docs/roadmap.md`): full CRUD for entities/logs/schedules, real
session auth, and a real frontend across all 5 domains (home, vehicle,
equipment, project, person). Nothing document- or AI-related exists yet
beyond the shared `Document` Pydantic model (`libs/shared/src/aria_shared/models/documents.py`)
sitting unused, and a `worker` service that only runs a `ping` task.

M2 implements the PRD §3 ingestion pipeline per `docs/roadmap.md`'s M2
bullet list and `docs/data-model.md` §6/§8: upload a file, store the raw
bytes, then OCR → chunk → embed it into Chroma asynchronously, with each
stage visible as a `processing_status` transition. Per the "strict
decoupling" principle, upload/list/view of documents must keep working via
pure Mongo CRUD even if Redis/worker/Chroma/Ollama are down — a document
just stays `pending` (or `failed`) until the pipeline can run.

**Exit criteria (from the roadmap, unchanged):** upload a manual/receipt
PDF, watch it move through `pending → ocr_complete → chunked → embedded`,
and see it listed against the entity it's attached to. No chat yet — this
milestone is purely the ingestion side.

---

## Key design decisions

**Storage — S3-compatible object storage, not a shared Docker volume.**
Locally that's MinIO (a new `docker-compose.yml` service); in production
it's real AWS S3. Both are `boto3.client("s3", ...)` with the same call
surface — the only thing that changes between environments is which
settings get passed to that constructor (`s3_endpoint_url` set to MinIO's
address locally, unset/AWS-default in production). This was a deliberate
pivot away from the shared-volume design floated earlier in this plan:
a Docker volume ties `core-api` and `worker` to the same host and would
mean rewriting file-handling logic in both services when moving to
production storage. Writing the `boto3` integration once now and testing
it against MinIO means the production swap is an environment-variable
change, not a code change. `storage_path` on the `Document` record becomes
the **S3 object key**: `{household_id}/{document_id}/{original_filename}`
(field name unchanged — a "path" reads naturally for an object key too, and
renaming it isn't otherwise motivated).

- **Client config**: `boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, aws_access_key_id=..., aws_secret_access_key=..., region_name=..., config=Config(s3={"addressing_style": "path"}) if settings.s3_endpoint_url else None)`.
  MinIO needs path-style addressing to resolve correctly without wildcard
  DNS; real S3 works fine with boto3's virtual-hosted-style default, so
  path-style is only forced when a custom endpoint is configured. This
  `if endpoint_url` branch is the entire prod/local difference.
- **Bucket bootstrap**: MinIO doesn't auto-create buckets. `core-api`'s
  existing `lifespan` (already doing `ensure_seed_household`) gains
  `ensure_bucket(s3_client, bucket)` — a `head_bucket` check, `create_bucket`
  on 404, both idempotent. No separate init container needed.
- **Downloads stay proxied through `core-api`, not presigned URLs** —
  `GET /documents/{id}/file` validates the session + household ownership
  first, *then* streams `s3_client.get_object(...)["Body"]` back as a
  `StreamingResponse`. A presigned URL would need its own expiry/scoping
  logic to not leak access beyond the requesting household, which is more
  moving parts than just proxying the bytes through an endpoint that
  already has auth wired up. Presigned URLs are a reasonable optimization
  later if proxying becomes a bandwidth bottleneck on `core-api`.
- **Worker fetches bytes into memory, not to a temp file** —
  `s3_client.get_object(...)["Body"].read()` gives raw bytes directly;
  `pdf2image.convert_from_bytes()` and `PIL.Image.open(BytesIO(...))` both
  accept in-memory bytes, so there's no temp-file bookkeeping/cleanup to
  get wrong. Fine at the file sizes `max_upload_bytes` already caps uploads
  to (25MB) — not appropriate if that cap grows by an order of magnitude
  later.
- **New shared dependency, not a new coupling** — both `core-api` and
  `worker` add `boto3` and their own small `s3.py` client wrapper (same
  "thin per-service client, no shared runtime lib" pattern as the
  Ollama/Chroma clients below) — they still only share *data* via Mongo,
  not a storage abstraction layer.

**Upload endpoint validates mime type up front** — the OCR stage only knows
how to handle PDF and common image formats, so `POST /documents` rejects
anything outside `{application/pdf, image/jpeg, image/png}` with a 400
*before* touching disk, rather than accepting arbitrary files and failing
asynchronously in the pipeline. `document_type` (manual/receipt/invoice/
photo/diagram/other) stays a separate user-supplied field — it's about what
the file *is*, not what format it's in.

**Entity linking is required, not optional** — a document must have
`entity_ids: list[str]` with at least one member, and every id must
reference a non-archived entity in the caller's household (same validation
shape as `logs.entity_id` today, extended to a list). Uploading to an
archived entity is rejected — consistent with M1's existing rule that
archived entities don't accept new logs/schedules either.

**Enqueue is fire-and-forget from `core-api`, not a hard dependency** —
`core-api` does not import `worker`'s Celery app (that would couple the two
services' deploys). It gets a lightweight standalone Celery *producer*
(`celery.Celery(broker=settings.celery_broker_url)`, no result backend) and
calls `.send_task("app.tasks.process_document.process_document", args=[document_id])`
after the Mongo insert + S3 upload succeed. The task payload is just the
`document_id` string — never raw file bytes through Redis. The `send_task`
call is wrapped in a try/except: if Redis is unreachable, the document is
still created as `pending` and the request still returns 201 — decoupling
holds. There's no manual "retry ingestion" button in M2; note it as a
natural fast-follow. (MinIO itself is a harder dependency: if it's down,
the upload request legitimately fails — there's no bytes to store. That's
no different from `core-api`'s existing hard dependency on Mongo being up;
it just means "document upload" specifically needs storage reachable, the
same way anything needs its database reachable. Redis/worker/Chroma/Ollama
being down is the case that must degrade gracefully, and does.)

**One Celery task does all four stages, not a chain of four** — OCR output
(per-page text) is only needed transiently to build chunks, and chunks are
only needed transiently to embed. Keeping it one task means that
intermediate data lives in Python variables, not in Mongo or on disk, which
is simpler than persisting OCR text somewhere between separate task hops.
The cost is coarser retry granularity (a failure at the embed stage
re-does OCR too) — acceptable for MVP volume; splitting into a real Celery
chain (with OCR text cached to the document's storage folder between hops)
is a reasonable fast-follow if documents get large enough for that to
matter. `Document.processing_status` is updated in Mongo after each stage
completes, so a client polling mid-run sees real progress, not a fake
sequence.

**Worker gets its own thin Ollama/Chroma clients, not a shared lib import**
— `ai-service/app/ollama.py` and `chroma.py` already do this (thin
`httpx`/`chromadb` wrappers, each ~15 lines). `worker` gets its own copies
under `app/ollama.py` / `app/chroma.py` rather than factoring them into
`aria-shared`. Small duplication, but it keeps each service's runtime
dependencies (and failure modes) independent — the whole point of
`aria-shared` is the *data* contract, not a shared runtime client.

**Worker uses `pymongo` (sync), not `motor` (async)** — Celery task
functions are plain sync functions; there's no concurrent-request pool to
justify async here the way there is in `core-api`. A sync `pymongo.MongoClient`
to read the `Document` doc and patch `processing_status` is simpler than
bridging `motor` into a sync task with `asyncio.run()` for no real benefit.

**Chunking is a pure, deterministic function** — `chunk_pages(pages: list[str]) -> list[Chunk]`
in `worker/app/logic/chunking.py`, no Celery/Mongo/network involved, so it's
unit-testable the same way `compute_next_due` was in M1. Algorithm:
- Split each page's OCR text into paragraphs on blank lines.
- A paragraph is treated as a **section header** if it's short (<80 chars),
  has no terminal punctuation, and is either ALL CAPS or Title Case. The
  most recent header seen carries forward as `section_header` metadata for
  subsequent chunks until a new one appears.
- Accumulate paragraphs into a buffer; flush a chunk once the buffer
  reaches ~1000 characters (or the page ends). Each flushed chunk records
  `text`, `page_number` (of its first paragraph), `section_header`
  (nullable), and `chunk_index` (sequential across the whole document).
- No overlap between chunks for v0 — flagged as a possible retrieval-quality
  improvement for M4, not required for M2's ingestion-only exit criteria.

**Chroma write shape matches `data-model.md` §8 exactly** — one
`documents` collection in Chroma, `ids=f"{document_id}:{chunk_index}"`,
`metadatas={mongo_document_id, page_number, section_header, chunk_index}`.
Nothing else reads this collection until M4.

**Failure handling** — any exception in the task (OCR, chunking, embed, or
Chroma write) is caught once at the top level, sets
`processing_status="failed"` + `processing_error=str(exc)` on the Mongo
doc, and does not retry. A failed document is still fully visible/
downloadable via the CRUD endpoints — only the semantic-search path is
missing, per the decoupling principle.

---

## File-by-file plan

### `docker/python-service.Dockerfile`
- Add `tesseract-ocr` and `poppler-utils` (apt) to the shared image — needed
  by `worker` for `pytesseract`/`pdf2image`. Installed unconditionally
  (image is already shared across all 3 Python services; conditional
  per-`SERVICE` apt logic isn't worth the complexity for one extra layer).

### `docker-compose.yml`
- New `minio` service: `image: minio/minio`, `command: server /data --console-address ":9001"`,
  ports `9000` (S3 API) / `9001` (console), volume `minio_data:/data`, env
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (dev defaults, e.g. `aria`/`aria-dev-secret`),
  healthcheck against `/minio/health/live`.
- `core-api`: env `CORE_API_S3_ENDPOINT_URL=http://minio:9000`,
  `CORE_API_S3_BUCKET=aria-documents`, `CORE_API_S3_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`
  (match MinIO's root user/password), `CORE_API_S3_REGION=us-east-1` (arbitrary — MinIO
  ignores it, boto3 requires *some* value), `CORE_API_CELERY_BROKER_URL=redis://redis:6379/0`;
  add `depends_on: minio/redis: condition: service_healthy`. No volume mount needed —
  storage is now over the network.
- `worker`: env `WORKER_MONGO_URI=mongodb://mongo:27017`, `WORKER_MONGO_DB_NAME=aria`,
  same `WORKER_S3_*` set as `core-api`'s (same bucket/credentials),
  `WORKER_CHROMA_HOST=chromadb`, `WORKER_CHROMA_PORT=8000`,
  `WORKER_OLLAMA_HOST=http://ollama:11434`,
  `WORKER_OLLAMA_MODEL=${OLLAMA_MODEL:-qwen3:14b}`; add
  `depends_on: mongo/chromadb/minio: service_healthy, ollama: service_started`.

### `services/core-api/pyproject.toml`
- Add `celery[redis]` (producer-only use — no worker/beat), `boto3`,
  `python-multipart` (FastAPI file upload support, if not already pulled in
  transitively) to `dependencies`; add `moto[s3]` to the `dev` group (fakes
  S3 in tests, no real MinIO/network needed in CI).

### `services/core-api/app/`
- `config.py` — add `s3_endpoint_url`, `s3_bucket`, `s3_access_key_id`,
  `s3_secret_access_key`, `s3_region`, `max_upload_bytes` (default 25MB),
  `celery_broker_url`.
- `celery_client.py` (new) — module-level lazy `Celery` producer instance +
  `enqueue_document_processing(document_id: str) -> None` wrapping
  `send_task` in a try/except that logs and swallows connection errors.
- `s3.py` (new) — lazy `boto3` S3 client singleton (path-style addressing
  when `s3_endpoint_url` is set) + `ensure_bucket()`, `upload(key, fileobj)`,
  `stream(key) -> Body`, `delete(key)`.
- `routers/documents.py` (new) — `POST /documents`, `GET /entities/{entity_id}/documents`,
  `GET /documents/{id}`, `GET /documents/{id}/file`, `DELETE /documents/{id}`.
- `schemas/documents.py` (new) — request/response DTOs (multipart form
  fields don't map cleanly to a single Pydantic body model in FastAPI, so
  the router declares `File`/`Form` params directly and assembles the DTO).
- `main.py` — wire the new router; `lifespan` gains `s3.ensure_bucket()`
  alongside the existing `ensure_seed_household()` call.
- `tests/test_documents_crud.py` (new) — upload validation (bad mime, empty
  `entity_ids`, archived entity, oversized file), list-by-entity scoping,
  download, delete (Mongo doc + S3 object both gone). Uses `moto`'s
  `mock_aws` to fake S3 (no real MinIO needed in CI); `enqueue_document_processing`
  mocked/patched so tests don't need Redis either.

### `services/worker/pyproject.toml`
- Add `pytesseract`, `pdf2image`, `pillow` (OCR), `httpx` (Ollama), `chromadb`
  (vector write), `pymongo` (sync Mongo), `boto3` (S3 fetch).

### `services/worker/app/`
- `config.py` — add `mongo_uri`, `mongo_db_name`, `s3_endpoint_url`,
  `s3_bucket`, `s3_access_key_id`, `s3_secret_access_key`, `s3_region`,
  `chroma_host`, `chroma_port`, `ollama_host`, `ollama_model`.
- `db.py` (new) — lazy `pymongo.MongoClient` singleton, mirroring `core-api`'s
  `db.py` pattern but sync.
- `s3.py` (new) — lazy `boto3` S3 client singleton (same addressing-style
  logic as `core-api`'s) + `download(key) -> bytes`.
- `ollama.py` (new) — thin `embed(text: str) -> list[float]` wrapper (sync
  `httpx.Client`, mirrors `ai-service/app/ollama.py`'s `/api/embed` call).
- `chroma.py` (new) — thin lazy `chromadb.HttpClient` singleton + `get_or_create_collection("documents")`.
- `logic/ocr.py` (new) — `extract_pages(file_bytes: bytes, mime_type: str) -> list[str]`
  (branches on `pdf2image.convert_from_bytes` vs `PIL.Image.open(BytesIO(...))`,
  both piped through `pytesseract.image_to_string`).
- `logic/chunking.py` (new) — `chunk_pages(pages: list[str]) -> list[Chunk]`,
  pure function per the algorithm above.
- `tasks/process_document.py` (new) — the 4-stage task: fetch `Document`
  from Mongo → download bytes from S3 → OCR (status → `ocr_complete`) →
  chunk (status → `chunked`) → embed each chunk + write to Chroma (status →
  `embedded`); catches any exception → `failed` + `processing_error`.
- `tests/test_chunking.py` (new) — pure-function unit tests for
  `chunk_pages` (header detection, size-based flushing, page-number
  tracking, no-overlap boundary cases). No Celery/Mongo/network needed.

### `services/frontend/src/`
- `api/documents.ts` — `uploadDocument(entityId, file, documentType)` (`FormData`,
  `credentials: 'include'`), `listEntityDocuments(entityId)`, `getDocument(id)`,
  `deleteDocument(id)`; `downloadUrl(id)` helper (plain `<a href>`, not fetched
  through `apiFetch`, so the browser handles the byte stream/`Content-Disposition`).
- `hooks/useEntityDocuments.ts` — TanStack Query, `refetchInterval` of ~2s
  *only* while any returned document is still `pending`/`ocr_complete`/`chunked`
  (stops polling once everything is `embedded` or `failed`).
- `hooks/useUploadDocument.ts`, `useDeleteDocument.ts` — mutations,
  invalidate `useEntityDocuments`' query key on success.
- `components/DocumentUploadForm.tsx` — file input + `document_type` select.
- `components/DocumentList.tsx` — row per document: filename, type, a
  status badge (reusing `StatusBadge`'s color pattern) keyed off
  `processing_status`, uploaded date, download link, delete button.
- `pages/EntityDetailPage.tsx` — add a "Documents" tab alongside the
  existing Logs/Schedules tabs, rendering `DocumentUploadForm` + `DocumentList`.

---

## Sequencing

1. `docker/python-service.Dockerfile` + `docker-compose.yml` changes first —
   nothing below can be manually verified without `minio`/apt-package wiring
   in place. Confirm `minio` comes up healthy and its console (`:9001`) is
   reachable before writing any client code against it.
2. `core-api`: `s3.py` (client + `ensure_bucket`), config, `celery_client.py`
   (producer only) — verify by hand that a bucket appears in the MinIO
   console on `core-api` startup, and that `send_task` doesn't blow up the
   request when Redis is briefly stopped.
3. `core-api`: `routers/documents.py` (upload, list, get, download, delete) +
   tests — verify manually via `/docs` and curl that a `Document` record
   appears in Mongo and the object appears in the MinIO console,
   `processing_status` starts `pending`.
4. `worker`: OCR + chunking as pure functions first (`logic/ocr.py`,
   `logic/chunking.py`) with their unit tests — no Celery/S3 wiring needed
   to validate these in isolation.
5. `worker`: `db.py`, `s3.py`, `ollama.py`, `chroma.py` thin clients.
6. `worker`: `tasks/process_document.py` wiring the above together — verify
   end-to-end by uploading a real PDF through `core-api` and watching
   `processing_status` advance in Mongo (`mongosh` or the `GET /documents/{id}`
   endpoint) and a new entry land in Chroma (`GET` via the Chroma REST API
   or `ai-service`'s existing health-check client, ad hoc).
7. Frontend: API client + hooks, then `DocumentUploadForm`/`DocumentList`,
   then wire the Documents tab into `EntityDetailPage`.

---

## Verification

**Manual walkthrough** matching the exit criteria exactly: open an entity's
detail page → upload a manual/receipt PDF → confirm it appears immediately
with status `pending` → watch (via the polling UI, no manual refresh) it
progress through `ocr_complete → chunked → embedded` → download it and
confirm the bytes round-trip correctly → delete it and confirm it
disappears from the list and the object is gone from the MinIO console.
Also: stop the `worker`/`redis`/`chromadb`/`ollama` containers (leave
`minio` up), confirm upload/list/download/delete on `core-api` still work
and the document simply stays `pending` (strict decoupling holds — MinIO is
in the same category as Mongo, a hard dependency for the CRUD path itself,
not an AI-adjacent one); upload a `.txt` file and confirm the 400
mime-type rejection; upload to an archived entity and confirm the 400.

**Automated**: `uv run pytest` in both `core-api` (upload validation, CRUD,
scoping — S3 calls faked via `moto`) and `worker` (chunking unit tests);
`npm run lint` + `npm run build` in `frontend`.

### Critical files
- `services/worker/app/tasks/process_document.py`
- `services/worker/app/logic/chunking.py`
- `services/core-api/app/routers/documents.py`
- `services/core-api/app/s3.py`
- `services/core-api/app/celery_client.py`
- `docker-compose.yml`
- `docker/python-service.Dockerfile`
- `libs/shared/src/aria_shared/models/documents.py`
