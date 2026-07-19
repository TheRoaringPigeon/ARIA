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
`maintenance` / `vehicle` / `research` / `general` via one non-streaming
`ollama.py::complete()` call (a plain "respond with one word" instruction,
*not* Ollama's native `tools` param — that has a documented bug against
Qwen3), then routes to a specialist node with its own persona and a
curated subset of two tools: `gather_household_context` (wraps
`entity_grounding.py::gather_entity_context()`, unchanged since M4/M5) and
`search_household_documents` (wraps `retrieval.py::retrieve_context()`,
unchanged since M4) — both still embed via the dedicated
`AI_SERVICE_EMBED_MODEL` and drop anything past
`AI_SERVICE_RAG_MAX_DISTANCE`, exactly as before. `maintenance`/`vehicle`
call `gather_household_context` unconditionally; `general` (the fallback
for an unclassifiable query) calls both tools unconditionally — identical
to the pre-M7 blanket behavior, so an ambiguous query never regresses;
`research` is the one specialist with a real bounded tool-choice loop
(`AI_SERVICE_AGENT_MAX_TOOL_CALLS`, default 2), deciding via a small
JSON-in-content protocol (same non-native-tools reasoning as the
supervisor) whether/what to search. Graph state is checkpointed to a
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
`core-api` beyond forwarded-cookie HTTP calls. One accepted gap carried
over from M4: Chroma's chunk metadata has no `household_id`, so document
retrieval isn't scoped per household — harmless today since exactly one
household exists.

`ai-service`'s CORS (`main.py`) is not wildcard/no-credentials — cookie
forwarding cross-origin requires an explicit
`allow_origins=[settings.frontend_origin]` + `allow_credentials=True`,
mirroring `core-api`'s own CORS setup exactly.

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

Eight services: `mongo`, `chromadb`, `redis`, `agent-store`, `ollama`
(infra) and `core-api`, `ai-service`, `worker`, `frontend` (app code).
`agent-store` (`redis/redis-stack-server`, M7) is deliberately separate
from `redis` (plain `redis:7-alpine`, used only for Celery's broker/
result backend) — LangGraph's Redis checkpointer needs the RediSearch/
RedisJSON modules stock Redis doesn't ship, and upgrading the
Celery-critical instance for an AI-layer feature would widen that
feature's blast radius past `ai-service`. A few things worth noting:

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
