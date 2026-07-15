from fastapi import FastAPI

from app.routers import health
from aria_shared.middleware import add_permissive_cors

app = FastAPI(title="ARIA ai-service")

add_permissive_cors(app)

app.include_router(health.router)
