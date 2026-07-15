from fastapi import FastAPI

from app.routers import entities, health
from aria_shared.middleware import add_permissive_cors

app = FastAPI(title="ARIA core-api")

add_permissive_cors(app)

app.include_router(health.router)
app.include_router(entities.router)
