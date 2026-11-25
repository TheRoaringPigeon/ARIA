import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from app import core_api_client, ollama
from app.config import settings

logger = logging.getLogger(__name__)

_FUZZY_ENTITY_MATCH_SYSTEM_PROMPT = (
    "You are matching a household member's message to one specific "
    "household entity, for a case where the message names something close "
    "to but not an exact match for any entity's stored name (e.g. "
    '"my Ranger" when the entity is actually named "2021 Ford Ranger").\n\n'
    "Given the message and the household's entities below (id, name, "
    "domain, tags), decide which single entity the message most plausibly "
    "refers to, or set entity_id to null only if the message clearly isn't "
    "about any entity in the list at all. Prefer picking your best guess "
    "over answering null — don't hold out for certainty."
)

# `format`-constrained (`ollama.complete_structured()`) — same reasoning as
# `agents/nodes.py`'s decision schemas (scaling-debt.md #15): sidesteps
# Ollama's native `tools` param bug against Qwen3 (ollama/ollama#14601) and
# guarantees valid JSON instead of the free-text "respond with ONLY a JSON
# object" prompting + `ModelAdapter.parse_tool_decision()` this used to
# need.
def fuzzy_entity_match_schema(entity_ids: list[str]) -> dict:
    """Built per request so `entity_id` can only be one of the candidates."""
    return {
        "type": "object",
        "properties": {"entity_id": {"type": ["string", "null"], "enum": [*entity_ids, None]}},
        "required": ["entity_id"],
    }

ATTR_LABELS: dict[str, dict[str, str]] = {
    "person": {
        "relationship": "Relationship",
        "company": "Company",
        "job_title": "Job title",
        "birthday": "Birthday",
    },
    "vehicle": {
        "make": "Make",
        "model": "Model",
        "year": "Year",
        "current_mileage": "Current mileage",
        "vin": "VIN",
        "license_plate": "License plate",
        "purchase_date": "Purchased",
    },
    "home": {
        "entity_type": "Type",
        "make": "Make",
        "model": "Model",
        "serial_number": "Serial number",
        "paint_brand": "Paint brand",
        "paint_code": "Paint code",
        "install_date": "Installed",
        "warranty_expires_at": "Warranty expires",
    },
    "equipment": {
        "make": "Make",
        "model": "Model",
        "serial_number": "Serial number",
        "purchase_date": "Purchased",
    },
    "project": {
        "start_date": "Started",
        "target_end_date": "Target end",
        "completed_date": "Completed",
        "budget_estimate": "Budget estimate",
    },
}

ATTR_EXCLUDED: dict[str, set[str]] = {
    "person": {"domain", "email", "phone"},
    "vehicle": {"domain"},
    "home": {"domain"},
    "equipment": {"domain"},
    "project": {"domain", "related_entity_ids"},
}


@dataclass
class EntityContext:
    id: str
    domain: str
    name: str
    tags: list[str]
    specs: dict[str, str]
    attrs: dict[str, str] | None
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


def _render_entities_for_fuzzy_match(entities: list[dict]) -> str:
    if not entities:
        return "(no household entities)"
    lines = []
    for entity in entities:
        tags = ", ".join(entity.get("tags", [])) or "none"
        lines.append(f"- {entity['id']}: {entity['name']} ({entity['domain']}) [tags: {tags}]")
    return "\n".join(lines)


def fuzzy_match_message(query: str, candidates: list[dict]) -> dict:
    return {
        "role": "user",
        "content": f"Household entities:\n{_render_entities_for_fuzzy_match(candidates)}\n\nMessage: {query}",
    }


async def resolve_fuzzy_entity_match(query: str, entities: list[dict]) -> list[dict]:
    """Fallback for when `find_matching_entities()`'s word-boundary pass
    finds nothing but the query still names something close to an entity
    (e.g. "my Ranger" when the entity is named "2021 Ford Ranger") — asks
    the model which single entity, if any, the query most likely refers
    to, given the full household entity list. Only ever called when the
    deterministic pass is empty (see `gather_entity_context`) — never
    overrides or extends a confident exact match.

    Degrades to `[]` on any failure (Ollama unreachable, an unparseable
    reply, or the model naming an `entity_id` that isn't actually in
    `entities` — a hallucination guard, not just trusting the model's own
    id) — same never-load-bearing contract as every other function here.
    """
    candidates = entities[: settings.entity_fuzzy_match_candidate_limit]
    if not candidates:
        return []

    def _extract_match(decision: dict) -> list[dict]:
        entity_id = decision.get("entity_id")
        if not entity_id:
            return []
        matched_entity = next((e for e in candidates if e["id"] == entity_id), None)
        return [matched_entity] if matched_entity is not None else []

    return await ollama.complete_structured_or_default(
        [
            {"role": "system", "content": _FUZZY_ENTITY_MATCH_SYSTEM_PROMPT},
            fuzzy_match_message(query, candidates),
        ],
        fuzzy_entity_match_schema([e["id"] for e in candidates]),
        extract=_extract_match,
        default=[],
        warning="fuzzy entity match failed, degrading to no match",
    )


def _build_attrs(entity: dict) -> dict[str, str] | None:
    """Labeled, allowlisted domain attributes for the prompt. Fields in
    ATTR_EXCLUDED (contact details, internal ids) never reach the model."""
    labels = ATTR_LABELS.get(entity.get("domain"), {})
    attributes = entity.get("attributes") or {}
    attrs = {
        label: str(attributes[key])
        for key, label in labels.items()
        if attributes.get(key) not in (None, "")
    }
    return attrs or None


async def _build_entity_context(cookie: str, entity: dict) -> EntityContext:
    logs, schedules = await asyncio.gather(
        core_api_client.list_entity_logs(cookie, entity["id"], limit=settings.entity_logs_limit),
        core_api_client.list_entity_schedules(cookie, entity["id"]),
    )
    return EntityContext(
        id=entity["id"],
        domain=entity["domain"],
        name=entity["name"],
        tags=entity.get("tags", []),
        specs=entity.get("specs", {}),
        attrs=_build_attrs(entity),
        logs=logs,
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
        if not matched and entities:
            # Only reached when *this* function owns the matching decision
            # (a caller that hands in its own `matched` — `propose_action_node`'s
            # write-path whitelist — never falls through to here, deliberately;
            # see docs/plans/fuzzy-entity-matching.md).
            matched = await resolve_fuzzy_entity_match(query, entities)

    if not matched:
        return []

    results = await asyncio.gather(
        *(_safe_build_entity_context(cookie, entity) for entity in matched)
    )
    return [context for context in results if context is not None]
