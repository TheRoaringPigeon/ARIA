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

```
docker compose up --build
```

- Frontend: http://localhost:5173
- core-api: http://localhost:8000 (docs at `/docs`)
- ai-service: http://localhost:8001 (docs at `/docs`)
- Mongo: localhost:27017
- Chroma: http://localhost:8002
- Ollama: http://localhost:11434

The `ollama` container pulls its model on first start (see
`docker/ollama/entry.sh`), so the first `docker compose up` will take longer
while the model downloads. It requests a GPU (`runtime: nvidia`) by default —
drop the `deploy`/`runtime` lines in `docker-compose.yml` for CPU-only.

The frontend's landing page hits both services' `/health` endpoints to confirm
the stack is wired up correctly.

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
