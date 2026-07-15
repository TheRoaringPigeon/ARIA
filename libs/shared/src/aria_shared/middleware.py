from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def add_permissive_cors(
    app: FastAPI,
    allow_origins: list[str] | None = None,
    allow_credentials: bool = False,
) -> None:
    """CORS for local dev. Defaults to wildcard/no-credentials (ai-service's
    case, which has no cookies to protect). Cookie-based auth requires a
    specific origin list — browsers reject `allow_credentials=True` combined
    with a wildcard origin — which is why core-api passes both explicitly.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins if allow_origins is not None else ["*"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
