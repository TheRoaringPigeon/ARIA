import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

T = TypeVar("T")


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.ollama_host, timeout=300.0)
    return _client


async def chat_stream(messages: list[dict]) -> AsyncIterator[dict]:
    """Yields Ollama's own NDJSON stream frames (one per content delta) as
    parsed dicts — `/api/chat` with `"stream": true` returns newline-
    delimited JSON, not SSE. Raises before yielding anything if the initial
    connection/response fails.
    """
    client = get_client()
    async with client.stream(
        "POST",
        "/api/chat",
        json={"model": settings.ollama_model, "messages": messages, "stream": True},
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            yield json.loads(line)


async def complete_structured(messages: list[dict], schema: dict) -> dict:
    """One-shot, non-streaming `/api/chat` call used for internal
    orchestration turns (agent routing, tool-choice decisions) that need a
    single short completion, not a user-facing streamed answer — distinct
    from `chat_stream()`, which is what the actual answer text still goes
    through. Constrains decoding to `schema` via Ollama's
    grammar-based `format` field and disables thinking. This is a
    genuinely different mechanism from Ollama's native `tools` param
    (which has a documented, model-specific bug against Qwen3 —
    ollama/ollama#14601 — see `app/agents/nodes.py`'s routing/tool-choice
    docstrings) — `format` grammar-constrains raw token decoding itself,
    so the model is structurally incapable of returning anything outside
    the schema, regardless of which model is loaded. This is what
    supersedes every free-text "respond with ONLY a JSON object" prompt +
    `ModelAdapter.parse_choice()`/`parse_tool_decision()` workaround this
    codebase used to need (scaling-debt.md #15).

    `think: False` — every caller of this is a short internal routing/
    tool-choice decision, none of which need visible chain-of-thought;
    skipping it also cuts real per-turn latency, since these calls
    currently sit in the critical path before the user-facing answer even
    starts streaming.

    `temperature: 0` — verified live against the real running stack that
    this matters: the same routing prompt/schema/query against the same
    model gave a *different* answer from one call to the next at Ollama's
    default sampling temperature (confirmed the same instability against
    both `qwen3.5:9b` and `qwen3:14b`, so it's not model-specific), then
    became fully reproducible once temperature was pinned to 0. A routing/
    tool-choice decision should be deterministic given the same input —
    unlike the user-facing answer (`chat_stream()`, uses Ollama's default
    temperature), nothing here benefits from sampling variety.

    Raises on an HTTP failure or an unparseable reply — every caller
    already wraps its own call in a broad try/except with a
    degrade-don't-fail fallback (see e.g. `supervisor_node`), so this
    doesn't need its own internal fallback.
    """
    client = get_client()
    resp = await client.post(
        "/api/chat",
        json={
            "model": settings.ollama_model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "think": False,
            "options": {"temperature": 0},
        },
    )
    resp.raise_for_status()
    parsed = json.loads(resp.json()["message"]["content"])
    if not isinstance(parsed, dict):
        # `format` grammar-constrains the schema's shape, but `schema["type"]
        # == "object"` doesn't stop `json.loads` from succeeding on some
        # other JSON-valid top-level value if the schema itself is ever
        # malformed — every caller assumes this always returns a `dict`
        # (caught in code review).
        raise ValueError(f"expected a JSON object from Ollama, got {type(parsed).__name__}")
    return parsed


async def complete_structured_or_default(
    messages: list[dict],
    schema: dict,
    extract: Callable[[dict], T],
    default: T,
    *,
    warning: str,
) -> T:
    """Shared try/`complete_structured`/except-Exception/degrade-to-default
    shape used by every schema-constrained decision call across this
    service (`agents/nodes.py`'s `supervisor_node`, `general_node`'s
    grounding gate, and `propose_action_node`'s decision call;
    `entity_grounding.resolve_fuzzy_entity_match`) — extracted so the four
    sites can't silently drift apart on what "degrade, don't fail" means
    here (caught in code review). `extract` runs inside the same
    try/except as the completion call itself, so a malformed-but-schema-
    valid reply (e.g. a decision dict missing a key `extract` assumes is
    there, or naming something that turns out not to exist) degrades the
    same way an unreachable Ollama does, rather than raising past this
    helper — matching `complete_structured()`'s own docstring, which
    already assumes every caller wraps it exactly this way.
    """
    try:
        decision = await complete_structured(messages, schema)
        return extract(decision)
    except Exception:
        logger.warning(warning, exc_info=True)
        return default


async def embed(text: str) -> list[float]:
    client = get_client()
    resp = await client.post(
        "/api/embed", json={"model": settings.embed_model, "input": text}
    )
    resp.raise_for_status()
    embeddings = resp.json().get("embeddings", [[]])
    return embeddings[0] if embeddings else []
