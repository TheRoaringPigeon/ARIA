from fastapi import FastAPI

from app.adapters import get_adapter
from app.routers import chat, health
from aria_shared.middleware import add_permissive_cors

app = FastAPI(title="ARIA ai-service")

add_permissive_cors(app)
get_adapter()  # fail fast on a misconfigured AI_SERVICE_MODEL_ADAPTER

app.include_router(health.router)
app.include_router(chat.router)
