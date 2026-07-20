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


def find_matching_entities(
    query: str, entities: list[dict], uncapped: bool = False
) -> list[dict]:
    """Word-boundary name/tag matching against a household's entity list —
    exported (not `_`-prefixed) so `agents/nodes.py`'s action-proposal
    prompt can reuse the exact same matching logic instead of duplicating
    the regex (see `propose_action_node`).

    Capped at `settings.entity_match_limit` — read dynamically per call
    (not bound as a literal default value) so tests can still monkeypatch
    the setting — unless `uncapped=True`. That cap exists to keep the
    read-path grounding prompt readable; it's the wrong thing to reuse as a
    write-path whitelist gate, where an entity beyond the cap would silently
    become impossible to reference even if named unambiguously (caught in
    code review — see `propose_action_node`, which passes `uncapped=True`
    for its own whitelist, but must still cap what it hands off to the
    read-path fallback grounding call).
    """
    matched = []
    for entity in entities:
        candidates = [entity["name"], *entity.get("tags", [])]
        if any(_word_boundary_match(candidate, query) for candidate in candidates):
            matched.append(entity)
    return matched if uncapped else matched[: settings.entity_match_limit]


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


async def fetch_entities(cookie: str) -> list[dict]:
    """The household's raw entity list, degrading to `[]` on any failure —
    no cookie check here (callers already have a non-`None` cookie in hand
    by the time they call this). Shared by `gather_entity_context` and
    `agents/nodes.py`'s action-path entity fetch
    (`_fetch_entities_for_action`) so the two `GET /entities` call sites
    can't drift on how a routine 401/expired-session degrade is logged
    versus a real core-api failure (caught in code review — they used to
    be two separately-maintained try/excepts, and the action path's copy
    had lost the 401-vs-other distinction).
    """
    try:
        return await core_api_client.list_entities(cookie)
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            logger.info("entity fetch skipped: session invalid or expired")
        else:
            logger.warning("entity fetch failed, degrading to none", exc_info=True)
        return []


async def gather_entity_context(
    query: str,
    cookie: str | None,
    entities: list[dict] | None = None,
    matched: list[dict] | None = None,
) -> list[EntityContext]:
    """Ground chat in the household's own entities/logs/schedules.

    Degrades to `[]` on any failure fetching the entity list itself — no
    cookie, an expired/invalid session, or core-api being unreachable — so
    entity grounding is never load-bearing for chat itself, mirroring
    `retrieval.py`'s contract for document grounding.

    `entities`, if given, skips the household entity-list fetch and matches
    against it directly — lets a caller that already fetched the list once
    (`agents/nodes.py`'s `propose_action_node`, via `gather_baseline_context`)
    avoid a second `GET /entities` round trip instead of calling this with
    just `(query, cookie)` and re-fetching.

    `matched`, if given, skips the word-boundary matching too — lets a
    caller that already computed its own match (`propose_action_node`,
    which needs the match itself to build its action-decision prompt)
    avoid running the identical regex scan a second time every
    action-classified turn (caught in code review).
    """
    if cookie is None:
        return []

    if matched is None:
        if entities is None:
            entities = await fetch_entities(cookie)
        matched = find_matching_entities(query, entities)

    if not matched:
        return []

    results = await asyncio.gather(
        *(_safe_build_entity_context(cookie, entity) for entity in matched)
    )
    return [context for context in results if context is not None]
