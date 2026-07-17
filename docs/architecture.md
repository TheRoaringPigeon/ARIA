# ARIA — Codebase Walkthrough

This explains what's actually implemented in the repo today: how the pieces
fit together, what each file does, and where the scaffolding stops and
"not yet built" begins. For the data model itself (collections, fields,
why they're shaped that way), see [`data-model.md`](data-model.md) — this
doc is about the code, not the schema.

## The big picture

ARIA is a home-ops assistant: track the "things" in a household (rooms,
systems, vehicles, equipment, projects), log what happened to them, know
what's coming due, and (eventually) let an LLM answer questions against
uploaded documents (manuals, receipts, invoices). All LLM inference runs
in-house against a local `ollama` container — no external API calls or keys.

Today, the repo is **scaffolding**: four services, a shared library, and
a local `ollama` container that prove the wiring works end to end — process
boundaries, health checks, one real CRUD endpoint, Docker networking, a
Celery worker that can execute a task. None of the actual product features
(AI chat, OCR, schedules logic) exist yet. That's the frame to read all of
this through.

```
pyproject.toml              # root uv workspace — lists the 4 members below
uv.lock                      # one lockfile for the whole workspace
libs/shared/                 # aria-shared: Pydantic models used by all 3 Python services
services/
  frontend/                  # React 18 + Vite + Tailwind + TanStack Query
  core-api/                  # FastAPI — Mongo CRUD, zero AI dependency
  ai-service/                # FastAPI — RAG/agents, talks to Chroma (not implemented yet)
  worker/                    # Celery — OCR/chunk/embed pipeline (not implemented yet)
docs/                        # this file + data-model.md
docker/                      # shared Dockerfile for the 3 Python services
docker-compose.yml           # wires all 6 containers together for local dev
```

## Why a `uv` workspace

`pyproject.toml` at the root just declares workspace members:

```toml
[tool.uv.workspace]
members = ["libs/shared", "services/core-api", "services/ai-service", "services/worker"]
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

None of these have any behavior yet — no validators beyond what Pydantic
gives for free, no methods besides the inherited `to_mongo()`. They're pure
data shape, which is the point: the same shape is what `core-api` reads
from Mongo, what `worker` will eventually write after OCR, and what
`ai-service` will eventually cite back to the user.

**`middleware.py`** — `add_permissive_cors(app)`, one function that both
FastAPI services call to slap wildcard CORS on for local dev. Explicitly
flagged in a docstring as temporary ("tighten once the frontend has a
fixed origin").

## `services/core-api` — the only service with real logic

This is the CRUD backbone. Structure:

```
app/main.py            # FastAPI app, registers routers
app/config.py           # env-driven settings (CORE_API_ prefix)
app/db.py               # Motor (async Mongo driver) client, lazily created
app/routers/health.py   # GET /health — pings Mongo
app/routers/entities.py # GET /entities — the one real endpoint
```

`config.py` uses `pydantic-settings` with `env_prefix="CORE_API_"`, so
`CORE_API_MONGO_URI` / `CORE_API_MONGO_DB_NAME` (set in
`docker-compose.yml`) populate `Settings.mongo_uri` / `mongo_db_name`
automatically, with sane defaults for running outside Docker.

`db.py` is a module-level singleton pattern: `get_client()` creates the
`AsyncIOMotorClient` once and reuses it (Motor clients pool connections
internally, so you don't want a new one per request); `get_db()` just
indexes into it by database name.

`routers/entities.py` has the one real business endpoint: `GET /entities`,
filtered by `household_id` (passed as a query param, not yet derived from
auth — the docstring calls this out explicitly as scaffolding), with
`limit`/`offset` pagination capped at `MAX_LIMIT = 200`. It queries Mongo
via Motor, then validates each raw dict through `EntityBase.model_validate(doc)`
— this is the point where a bare Mongo document becomes a typed,
discriminated-union-validated Pydantic object before FastAPI serializes it
back out as JSON.

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

`POST /chat` (M3, grounded as of M4) takes a list of `{role, content}`
messages (`role` restricted to `user`/`assistant` — a client can't inject a
`system` message). Before calling Ollama, `app/retrieval.py::retrieve_context()`
embeds the latest user message (via a dedicated `AI_SERVICE_EMBED_MODEL`,
separate from the chat model — chat-tuned models make unreliable
embeddings for anything but near-exact text) and runs a similarity search
against Chroma's `documents` collection (top `AI_SERVICE_RAG_TOP_K` chunks,
default 4, dropping anything past `AI_SERVICE_RAG_MAX_DISTANCE` — `n_results`
alone always returns `rag_top_k` chunks even when nothing in the corpus is
actually related). `routers/chat.py::build_system_prompt()` folds
whatever comes back into the system message — an instruction to use the
excerpts if relevant, or an "no relevant documents" caveat if retrieval
returned nothing — then forwards to `ollama.chat()` and returns the
model's reply. Still no persistence: each request is stateless, nothing is
written to Mongo or Chroma. A `ModelAdapter` seam (`app/adapters/`,
selected via `AI_SERVICE_MODEL_ADAPTER`, default `qwen`) post-processes
the raw model output before it's returned — `qwen3:14b` prefixes replies
with a `<think>...</think>` reasoning block that `QwenAdapter` strips, so
swapping models later doesn't require touching the router. The endpoint
is unauthenticated at the service level (no session/db dependency exists
in `ai-service` at all) — gating is frontend-only, consistent with the
"strict decoupling" principle: `ai-service` still depends on nothing
from `core-api`. Retrieval failure (Ollama or Chroma unreachable, or an
empty collection) degrades to ungrounded, M3-style chat rather than
failing the request — `retrieve_context()` catches broadly and logs a
warning, the same pattern `core-api/app/celery_client.py` uses for its
fire-and-forget task enqueues. One accepted gap: Chroma's chunk metadata
has no `household_id`, so retrieval isn't scoped per household — harmless
today since exactly one household exists, but real debt once
multi-household ships (see `roadmap.md`'s M4 note).

Chat is also grounded in the household's own entities/logs/schedules, not
just uploaded documents. `app/entity_grounding.py::gather_entity_context()`
word-boundary-matches the latest user message against every entity's
`name`/`tags` (case-insensitive, capped at `AI_SERVICE_ENTITY_MATCH_LIMIT`),
then fetches each match's logs (capped at `AI_SERVICE_ENTITY_LOGS_LIMIT`,
most recent first) and schedules via `app/core_api_client.py` — a thin
async client that forwards the browser's `aria_session` cookie straight to
core-api's existing `GET /entities`/`GET /entities/{id}/logs`/
`GET /entities/{id}/schedules` endpoints rather than validating the
session itself (validating a session is inherently a Mongo lookup by
opaque token — `ai-service` still opens no Mongo connection of its own;
`aria-auth` is a dependency here purely for the `SESSION_COOKIE_NAME`
constant). `build_system_prompt()` renders matched entities into a
"Relevant household records" section alongside M4's document excerpts.
This is why `ai-service`'s CORS (`main.py`) is no longer wildcard/
no-credentials — cookie forwarding cross-origin requires an explicit
`allow_origins=[settings.frontend_origin]` + `allow_credentials=True`,
mirroring `core-api`'s own CORS setup exactly. Degrades to ungrounded
chat on every failure axis (no cookie, an expired session, core-api
unreachable) the same way retrieval does — see `roadmap.md`'s "Household
data grounding" entry for the exact log-severity breakdown.

## `services/worker` — Celery skeleton

`celery_app.py` constructs the `Celery` app pointed at Redis (broker on
db 0, results on db 1) and calls `autodiscover_tasks(["app"])` so any
`@celery_app.task` decorated function under `app/` gets registered
automatically. `tasks/ping.py` is the only task: returns `"pong"`, proving
the worker can pick up and execute a task from the broker. The real
OCR → chunk → embed pipeline described in the data model doesn't exist
yet.

## `services/frontend` — one page, wired to both APIs

React 19 + Vite 8 + Tailwind 4 + TanStack Query. `main.tsx` sets up a
`QueryClient` and renders `<App />`. `App.tsx` is the entire UI: a
`useServiceHealth(name, url)` hook wraps `useQuery` to fetch
`${url}/health` from each backend (no retry — a dead service should show
red immediately, not hang retrying), and `ServiceStatusCard` renders a
colored status dot (gray while pending, red on error, green on success)
plus the raw JSON health response. The two cards point at `core-api` and
`ai-service` via `VITE_CORE_API_URL`/`VITE_AI_SERVICE_URL` (env vars
injected by `docker-compose.yml`, defaulting to `localhost:8000`/`8001`
for running outside Docker). This page's whole job is to prove the
frontend can actually reach both backends — there's no real feature UI
yet.

## `docker-compose.yml` — how it all runs together

Seven services: `mongo`, `chromadb`, `redis`, `ollama` (infra) and
`core-api`, `ai-service`, `worker`, `frontend` (app code). A few things
worth noting:

- `ollama` runs the actual LLM (default `qwen3:14b`, overridable via the
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

Implemented and working end-to-end:
- Cross-service shared schema via the `uv` workspace + `aria-shared`.
- `core-api`: Mongo connectivity, health check, one real read endpoint
  (`GET /entities`) with pagination and discriminated-union validation.
- `ai-service`/`worker`: process/network scaffolding and health/ping only.
- `frontend`: dev server, Tailwind, TanStack Query, and a health-check
  dashboard hitting both APIs.
- Full local dev stack via `docker compose up --build`, with correct
  startup ordering and hot-reload.

Explicitly not built yet (per code comments and the README):
- Auth / household-scoping derived from a session rather than a query param.
- Any write endpoints (`POST`/`PATCH`/`DELETE`) for entities, logs,
  schedules, or documents.
- The `schedules` due-date recompute logic described in `data-model.md` §5.
- Citations / streaming / LangGraph agents in `ai-service` (`POST /chat` is
  retrieval-grounded as of M4 — see above — but answers carry no visible
  source reference yet, and responses aren't streamed).
- OCR/chunk/embed tasks in `worker` (only the `ping` task exists).
- Any real frontend feature UI beyond the health dashboard.
