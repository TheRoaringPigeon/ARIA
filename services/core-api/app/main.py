from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import get_db
from app.routers import auth, entities, health, logs, schedules
from app.seed import ensure_seed_household
from aria_shared.middleware import add_permissive_cors


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_seed_household(get_db())
    yield


app = FastAPI(title="ARIA core-api", lifespan=lifespan)

add_permissive_cors(app, allow_origins=[settings.frontend_origin], allow_credentials=True)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(entities.router)
app.include_router(logs.router)
app.include_router(schedules.router)
