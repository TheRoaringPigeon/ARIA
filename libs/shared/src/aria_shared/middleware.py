from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def add_permissive_cors(app: FastAPI) -> None:
    """Wildcard CORS for local dev. Tighten once the frontend has a fixed origin."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
