# ARIA — Codebase Walkthrough

This explains what's implemented in the repo today: how the pieces fit
together and what each part does. For the data model itself (collections,
fields, why they're shaped that way), see [`data-model.md`](data-model.md) —
this doc is about the code, not the schema.

## The big picture

ARIA is a home-ops assistant: track the "things" in a household (home
systems, vehicles, equipment, projects, people), log what happened to them,
know what's coming due, ingest the documents that go with them (manuals,
receipts, invoices, phone scans), and let household members ask an LLM
questions grounded in all of that. All LLM inference and embedding runs
in-house against a local `ollama` container. The only external calls are
the research agent's optional web search (Brave) and weather (Open-Meteo)
tools.

```
pyproject.toml              # root uv workspace — lists the 5 members below
uv.lock                      # one lockfile for the whole workspace
libs/shared/                 # aria-shared: Pydantic models used by every Python service
libs/auth/                   # aria-auth: sessions, passwords, roles, permissions, sharing
services/
  frontend/                  # React 19 + Vite + Tailwind + TanStack Query, PWA
  core-api/                  # FastAPI — Mongo CRUD, auth, documents, zero AI dependency
  ai-service/                # FastAPI — RAG chat, LangGraph agents, MCP server
  worker/                    # Celery + Beat — OCR/chunk/embed pipeline, scheduled sweeps
docs/                        # design docs, plans, debt registers
docker/                      # shared Dockerfile for the Python services, Ollama entrypoint
docker-compose.yml           # wires the dev stack together
```

## Why a `uv` workspace

`pyproject.toml` at the root just declares workspace members:

```toml
[tool.uv.workspace]
members = ["libs/shared", "libs/auth", "services/core-api", "services/ai-service", "services/worker"]
```

There's no installable root package — this file exists purely so `uv` resolves
all three services and the shared library *together* into one `uv.lock`.
Concretely, that means `aria-shared` can't drift into three different
versions across services; if you bump a dependency in one service, `uv`
re-resolves the whole graph and the lockfile records what everyone actually
gets. Each service still has its own `pyproject.toml` (its own dependency
list, its own Docker image) — the workspace just shares resolution and the
lockfile, not the runtime.

`docker/python-service.Dockerfile` is one Dockerfile parameterized by a
`SERVICE` build arg (`core-api` / `ai-service` / `worker`). It copies the
root `pyproject.toml` + `uv.lock` + `libs/shared` + all three services'
`pyproject.toml` files first (so `uv sync --frozen` can resolve the whole
workspace), *then* copies in the actual source for just the one service
being built. That ordering is a Docker layer-caching trick: dependency
installation is cached until any `pyproject.toml` changes, independent of
how often that service's own code changes.

## `libs/shared` (`aria_shared`) — the one canonical schema

Every domain model lives here once and is imported by `core-api`,
`ai-service`, and `worker` as a workspace dependency (`aria-shared = { workspace = true }`
in each service's `pyproject.toml`). This is what the README calls "one
canonical schema, independently deployable services."

**`types.py`** — the foundation two other things build on:

- `PyObjectId`: Mongo's `_id` is a `bson.ObjectId`, which Pydantic doesn't
  natively understand. This is a `str`-typed annotation with a
  `BeforeValidator` (accept an `ObjectId` or a string, coerce to `str`) and
  a `PlainSerializer` (always emit as `str`). Every model's `id` field uses
  this type so ObjectIds become plain strings the moment they cross into
  Python/JSON, and nothing downstream (FastAPI response models, JSON
  serialization) has to special-case bson types.
- `MongoBaseModel`: the base class every stored document inherits.
  `populate_by_name=True` lets Python code use `.id`, `.household_id`, etc.
  while the model still reads/writes Mongo's `_id` field via each subclass's
  `Field(alias="_id")`. `to_mongo()` is `model_dump(by_alias=True)` — the
  one place that translates a Python model back into the `_id`-keyed dict
  Motor expects to insert.

**`models/`** — one file per collection from the data model, each a
straightforward Pydantic translation of what's documented in
`data-model.md`:

- `entities.py` — `EntityBase` plus the four domain-specific attribute
  models (`HomeAttrs`, `VehicleAttrs`, `EquipmentAttrs`, `ProjectAttrs`).
  Unlike the doc's "TBD" note on discriminator wiring, the code *does*
  wire it up: `EntityAttributes = Annotated[Union[...], Field(discriminator="domain")]`.
  Each attrs model pins its own `domain` field to a `Literal` default (e.g.
  `Literal["vehicle"] = "vehicle"`), which is what lets Pydantic dispatch
  the right sub-model based on the `domain` value in the payload.
- `household.py` — `Household` and `User`, minimal.
- `logs.py` — `LogEntry`, the unified, `type`-discriminated timeline
  collection.
- `schedules.py` — `Schedule`, the forward-looking recurring-maintenance
  model.
- `documents.py` — `Document`, matching the doc's OCR pipeline states
  (`pending → ocr_complete → chunked → embedded`, or `failed`).

The models are pure data shape, which is the point: the same shape is
what `core-api` reads from and writes to Mongo, what `worker` writes after
OCR, and what `ai-service` cites back to the user.
`models/entities/export_ts.py` generates the frontend's
`src/domains/generated.ts` from these models, and
`tests/test_export_ts.py` (also a pre-commit hook) fails if the committed
file has drifted from them.

**`middleware.py`** — `add_permissive_cors(app, allow_origins=..., allow_credentials=...)`,
called by both FastAPI services with the configured frontend origin and
credentials enabled, since cookie auth cross-origin can't use a wildcard.

## `libs/auth` (`aria_auth`) — sessions, roles, permissions

Shared by `core-api` and `ai-service`: password hashing, the session
cookie and `SessionContext`, the role-based `PERMISSIONS` registry
(`owner` vs. `member`), and the per-entity sharing rules
(`has_shared_access`). `worker` deliberately mirrors the sharing rule
instead of importing it, so it stays free of FastAPI/Motor dependencies.

## `services/core-api` — the CRUD and auth backbone

```
app/main.py             # FastAPI app, registers routers, ensures indexes and seed data
app/config.py           # env-driven settings (CORE_API_ prefix)
app/db.py               # Motor (async Mongo driver) client, lazily created
app/indexes.py          # idempotent index creation, incl. TTL on sessions
app/seed.py             # opt-in first-owner seeding
app/rate_limit.py       # Redis-backed limits on login/signup
app/s3.py               # boto3 client for S3-compatible object storage
app/celery_client.py    # enqueues worker tasks without importing worker code
app/logic/schedules.py  # due-date recompute when a matching log lands
app/routers/            # auth, users, households, entities, logs, schedules, documents, health
app/templates/          # HTML template for the per-entity PDF export (WeasyPrint)
app/migrations/         # idempotent one-off migrations (see docs/operations.md)
```

`config.py` uses `pydantic-settings` with `env_prefix="CORE_API_"`, with
sane defaults for running outside Docker. `db.py` is a module-level
singleton: `get_client()` creates the `AsyncIOMotorClient` once and reuses
it, since Motor pools connections internally.

Every route derives `household_id` from the session, never from the
request. Entity, log, and document lists are offset-paginated with hard caps. Entity delete is a soft "trash" with a grace period. The
restore and purge cascades (logs, schedules, documents, object storage,
vector chunks) run in Mongo transactions, and `worker`'s hourly sweep does
the final purge in batches. Raw Mongo documents are validated through the
shared Pydantic models (`EntityBase.model_validate(doc)` and friends), so
a bare document becomes a typed, discriminated-union-validated object
before FastAPI serializes it back out.

`routers/health.py`'s `GET /health` actually pings Mongo (`admin.command("ping")`)
and reports the result rather than just returning a static "ok" — so a
health check failure tells you *which* dependency is down, not just that
something is.

## `services/ai-service` — RAG-grounded chat

Same shape as `core-api` (`main.py`, `config.py`, `routers/health.py`) but
a few files deeper: `chroma.py` sets up a lazy singleton `chromadb.HttpClient`
pointed at the `chromadb` container (`get_documents_collection()` returns
its `documents` collection — the same one `worker` writes chunks into),
and `ollama.py` sets up a lazy singleton `httpx.AsyncClient` pointed at the
`ollama` container (`chat()`/`embed()` helpers hitting `/api/chat` and
`/api/embed`) — no external LLM API or key involved. `/health` pings both:
Chroma via `client.heartbeat()` (run via `asyncio.to_thread` since that
client is synchronous) and Ollama via `GET /api/tags`.

`POST /chat` (M3, grounded as of M4, agent-routed as of M7) takes a list
of `{role, content}` messages (`role` restricted to `user`/`assistant` —
a client can't inject a `system` message), streamed back as
`text/event-stream` (M6) with SSE events `agent`, `citations`, `thinking`,
`token`, `error` (`ollama.py::chat_stream()` parses Ollama's NDJSON
framing; a `StreamFilter` — `app/adapters/` — classifies each delta as
reasoning vs. answer so `<think>...</think>` content streams live as a
temporary preview instead of ending up in the permanent message).

As of M7, `routers/chat.py::chat()` no longer gathers context in one
fixed, blanket step — it hands the query to a LangGraph `StateGraph`
(`app/agents/`): a `supervisor` node classifies the query into
`maintenance` / `vehicle` / `research` / `general` / `action` via one
non-streaming `ollama.py::complete_structured()` call, then routes to a
specialist node with its own persona and a curated subset of two tools:
`gather_household_context` (wraps
`entity_grounding.py::gather_entity_context()`, unchanged since M4/M5) and
`search_household_documents` (wraps `retrieval.py::retrieve_context()`,
unchanged since M4) — both still embed via the dedicated
`AI_SERVICE_EMBED_MODEL` and drop anything past
`AI_SERVICE_RAG_MAX_DISTANCE`, exactly as before. `maintenance`/`vehicle`
call `gather_household_context` unconditionally; `general` (the fallback
for an unclassifiable query) calls both tools unconditionally — identical
to the pre-M7 blanket behavior, so an ambiguous query never regresses;
`research` is the one specialist with a real bounded tool-choice loop
(`AI_SERVICE_AGENT_MAX_TOOL_CALLS`, default 2), deciding via
`complete_structured()` (same as the supervisor) whether/what to search.

As of sd15 (`docs/scaling-debt-plans/sd15-*.md`), every one of these
internal decisions — the supervisor's classification, the research loop's
tool choice, the write-path's log/schedule decision, the general-node
grounding gate — goes through `ollama.py::complete_structured()`, which
sets Ollama's `format` field to a JSON Schema (grammar-constrained
decoding, forcing the reply to conform) and `think: false`. This is a
different mechanism from Ollama's native `tools` param, which has a
documented bug against Qwen3 (ollama/ollama#14601) — `format` isn't
affected by it and works the same regardless of which model is loaded, so
none of these call sites do their own free-text parsing anymore (no
per-model `ModelAdapter.parse_choice()`/`parse_tool_decision()` seam, no
word-boundary scanning, no markdown-fence stripping). `ModelAdapter`
(`app/adapters/`) now only owns `normalize_response()`/
`create_stream_filter()` — genuinely still model-specific, since the
user-facing streamed answer is real freeform prose, not schema-constrained.
Graph state is checkpointed to a
dedicated `agent-store` Redis Stack service — **not** the plain `redis`
Celery uses — because `langgraph-checkpoint-redis` needs the
RediSearch/RedisJSON modules stock Redis doesn't ship; this keeps a bug
or outage in agent orchestration from ever touching Celery's queue.
`build_system_prompt()` (unchanged logic, now takes a `persona` argument)
folds whatever the chosen specialist gathered into the system message —
an instruction to use the excerpts/records if relevant, or a "no relevant
X" caveat otherwise — then the router's existing M6 streaming code (fully
untouched) calls Ollama for the actual answer.

Strict decoupling for this new layer: any failure in the graph itself
(Redis unreachable, a node raising) is caught around the whole
routing step, logging a warning and falling back to calling
`gather_entity_context()`/`retrieve_context()` directly — the exact
pre-M7 blanket behavior, general persona, no `agent` SSE frame at all —
rather than failing the request. `ai-service` still opens no Mongo
connection of its own (`aria-auth` remains a dependency purely for the
`SESSION_COOKIE_NAME` constant) and still depends on nothing from
`core-api` beyond forwarded-cookie HTTP calls. Document retrieval is
scoped per household: chunks carry `household_id` metadata and every
Chroma query filters on it.

Write actions requested in chat (`action` specialist) don't execute
directly. `propose_action_node` builds a `create_log`/`create_schedule`
call, the graph pauses on a LangGraph interrupt, and the frontend shows a
confirmation card. Only a confirm resumes the graph and performs the write
through `core-api` with the user's forwarded session. The same two tools
are exposed to external agents by `app/mcp_server.py` (FastMCP, compose
service `mcp-server`).

`ai-service`'s CORS (`main.py`) is not wildcard/no-credentials — cookie
forwarding cross-origin requires an explicit
`allow_origins=[settings.frontend_origin]` + `allow_credentials=True`,
mirroring `core-api`'s own CORS setup exactly.

## `services/worker` — ingestion pipeline and scheduled jobs

`celery_app.py` constructs the `Celery` app pointed at Redis (broker on
db 0, results on db 1) and autodiscovers tasks under `app/`. Celery Beat
(compose service `worker-beat`) drives the periodic ones.

- `process_document` — the ingestion pipeline. Fetches the upload from
  object storage, runs Tesseract OCR (`logic/ocr.py`), splits the text into
  deterministic, section-header-aware chunks that keep their page numbers
  (`logic/chunking.py`), embeds each chunk through Ollama, and writes them
  to Chroma as `{document_id}:{chunk_index}` with household and page
  metadata. It advances `Document.processing_status` at each stage. For
  phone scans, it also rewrites the PDF in place with an invisible OCR text
  layer so the exported file is searchable.
- `finalize_document_draft` — assembles a multi-page phone photo capture
  into one PDF document.
- `delete_document` — removes every trace of a document (Chroma chunks,
  object storage, Mongo), safe to retry.
- `send_overdue_digest` (daily) — emails each user the overdue schedules
  they can see.
- `purge_expired_trash`, `purge_expired_upload_drafts` (hourly) — batched
  sweeps for trashed entities past their grace period and abandoned
  capture drafts.

## `services/frontend` — React PWA

React 19 + Vite 8 + Tailwind 4 + TanStack Query, built as an installable
PWA (`vite-plugin-pwa`, Workbox). Pages cover login, signup, and invite
acceptance; entity lists and detail views per domain (logs, schedules,
documents, upload, phone capture, PDF export); a "due soon" view; a
recently-deleted (trash) view; a profile page; and the chat page, which
consumes the SSE stream, renders citations and live agent progress, and
shows confirm/cancel cards for proposed actions. Log entry works offline:
the service worker (`sw.ts`) queues failed log `POST`s with Workbox
background sync and replays them when the connection returns. The domain
types in `src/domains/generated.ts` are generated from `libs/shared` (see
above).

## `docker-compose.yml` — how it all runs together

Infra services: `mongo` (plus a one-shot `mongo-init` that sets up the
single-node replica set transactions require), `chromadb`, `redis`,
`agent-store`, `minio` (S3-compatible object storage), and `mailpit` (a dev
SMTP sink for the digest emails). App services: `core-api`, `ai-service`,
`mcp-server` (opt-in `mcp` profile), `worker`, `worker-beat`, and
`frontend`. `ollama` lives in its own `docker-compose.llm.yml`, shared by
the dev and prod stacks.
`agent-store` (`redis/redis-stack-server`, M7) is deliberately separate
from `redis` (plain `redis:7-alpine`, used only for Celery's broker/
result backend) — LangGraph's Redis checkpointer needs the RediSearch/
RedisJSON modules stock Redis doesn't ship, and upgrading the
Celery-critical instance for an AI-layer feature would widen that
feature's blast radius past `ai-service`. A few things worth noting:

- `ollama` runs the actual LLM (default `qwen3.5:9b`, overridable via the
  `OLLAMA_MODEL` env var) — `docker/ollama/entry.sh` waits for the server
  to come up, pulls the model if it isn't already cached in the
  `ollama_data` volume, then keeps the server in the foreground. It
  requests a GPU (`deploy.resources.reservations.devices` + `runtime:
  nvidia`) by default.

- Each infra container has a `healthcheck`, and every app container that
  depends on one uses `condition: service_healthy` rather than just
  `depends_on` — so e.g. `core-api` won't start until Mongo actually
  answers a ping, not just until the container process has started.
- The Chroma healthcheck is a workaround: the official image ships no
  `curl`/`wget`/`python3`, so it probes the port directly via bash's
  `/dev/tcp/localhost/8000` pseudo-device instead of an HTTP call.
- Each Python service bind-mounts its own `app/` directory plus
  `libs/shared/src` over what got baked into the image, and runs
  `uvicorn --reload` / Celery in a way that picks up live edits —
  so `docker compose up` gives you hot-reload dev, not just a static
  built image.
- `core-api`/`ai-service`/`worker` all get their settings purely through
  environment variables matching each service's `env_prefix`, which is
  the same settings mechanism as running locally without Docker — Compose
  isn't doing anything the services don't already support.

## Where things stand

Milestone-by-milestone status lives in [`roadmap.md`](roadmap.md). Known
scaling and robustness gaps are ranked in
[`scaling-debt.md`](scaling-debt.md), and items that need a live AWS
environment are in [`aws-migration.md`](aws-migration.md). This doc
describes the code as it is; when a section here disagrees with the code,
the code wins and this doc should be updated.
