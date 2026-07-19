import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.adapters import get_adapter
from app.agents import get_graph
from app.config import settings
from app.routers import chat, health
from aria_shared.middleware import add_permissive_cors

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort warmup: builds the agent graph — including the Redis
    # checkpointer's index setup (real `FT.CREATE`-equivalent I/O against
    # `agent-store`) — now, at process startup, instead of inside whichever
    # user's request happens to be first (caught in code review). Failure
    # here isn't fatal: the app still starts, and `get_graph()`'s lazy-
    # singleton lock falls back to building it on the first real request,
    # same as before this hook existed — so a slow/unavailable `agent-store`
    # at boot degrades to the pre-existing lazy-build behavior, it doesn't
    # crash startup.
    try:
        await get_graph()
    except Exception:
        logger.warning(
            "eager agent-graph warmup failed at startup, falling back to "
            "lazy build on first request",
            exc_info=True,
        )
    yield


app = FastAPI(title="ARIA ai-service", lifespan=lifespan)

add_permissive_cors(app, allow_origins=[settings.frontend_origin], allow_credentials=True)
get_adapter()  # fail fast on a misconfigured AI_SERVICE_MODEL_ADAPTER

app.include_router(health.router)
app.include_router(chat.router)
