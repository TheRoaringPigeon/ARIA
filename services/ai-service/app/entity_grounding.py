import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from app import core_api_client
from app.config import settings

logger = logging.getLogger(__name__)

_PERSON_ATTR_LABELS = {
    "relationship": "Relationship",
    "company": "Company",
    "job_title": "Job title",
    "birthday": "Birthday",
}


@dataclass
class EntityContext:
    id: str
    domain: str
    name: str
    tags: list[str]
    specs: dict[str, str]
    person_attrs: dict[str, str] | None
    logs: list[dict]
    schedules: list[dict]


def _word_boundary_match(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def _find_matching_entities(query: str, entities: list[dict]) -> list[dict]:
    matched = []
    for entity in entities:
        candidates = [entity["name"], *entity.get("tags", [])]
        if any(_word_boundary_match(candidate, query) for candidate in candidates):
            matched.append(entity)
    return matched[: settings.entity_match_limit]


def _build_person_attrs(entity: dict) -> dict[str, str] | None:
    if entity.get("domain") != "person":
        return None
    attributes = entity.get("attributes", {})
    person_attrs = {
        label: str(attributes[key])
        for key, label in _PERSON_ATTR_LABELS.items()
        if attributes.get(key)
    }
    return person_attrs or None


async def _build_entity_context(cookie: str, entity: dict) -> EntityContext:
    logs, schedules = await asyncio.gather(
        core_api_client.list_entity_logs(cookie, entity["id"]),
        core_api_client.list_entity_schedules(cookie, entity["id"]),
    )
    return EntityContext(
        id=entity["id"],
        domain=entity["domain"],
        name=entity["name"],
        tags=entity.get("tags", []),
        specs=entity.get("specs", {}),
        person_attrs=_build_person_attrs(entity),
        logs=logs[: settings.entity_logs_limit],
        schedules=schedules,
    )


async def _safe_build_entity_context(cookie: str, entity: dict) -> EntityContext | None:
    """Isolates one matched entity's failure from the rest of the batch —
    without this, `asyncio.gather` in `gather_entity_context` would let one
    entity's 404 (e.g. deleted mid-request) or transient error wipe out
    every other successfully-matched entity's context too.
    """
    try:
        return await _build_entity_context(cookie, entity)
    except Exception:
        logger.warning(
            "failed to fetch context for entity %s, dropping it from grounding",
            entity.get("id"),
            exc_info=True,
        )
        return None


async def gather_entity_context(query: str, cookie: str | None) -> list[EntityContext]:
    """Ground chat in the household's own entities/logs/schedules.

    Degrades to `[]` on any failure fetching the entity list itself — no
    cookie, an expired/invalid session, or core-api being unreachable — so
    entity grounding is never load-bearing for chat itself, mirroring
    `retrieval.py`'s contract for document grounding.
    """
    if cookie is None:
        return []

    try:
        entities = await core_api_client.list_entities(cookie)
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            logger.info("entity grounding skipped: session invalid or expired")
        else:
            logger.warning(
                "entity grounding failed, degrading to ungrounded chat", exc_info=True
            )
        return []

    matched = _find_matching_entities(query, entities)
    if not matched:
        return []

    results = await asyncio.gather(
        *(_safe_build_entity_context(cookie, entity) for entity in matched)
    )
    return [context for context in results if context is not None]
