import hashlib
import json
from datetime import date

PROMPT_VERSIONS: dict[str, int] = {
    "supervisor": 1,
    "grounding_gate": 1,
    "research_tool": 1,
    "action_target": 2,
    "log_fields": 1,
    "schedule_kind": 1,
    "schedule_detail": 4,
    "last_done_date": 1,
    "fuzzy_entity_match": 1,
    "personas": 1,
    "answer_context": 1,
}


_SAMPLE_ENTITY = {"id": "<id>", "name": "<name>", "domain": "<domain>", "tags": ["<tag>"]}
_SAMPLE_DAY = date(2026, 1, 7)


def prompt_contents() -> dict[str, tuple]:
    """Everything sent to the model under each versioned name: prompt text,
    the definitions formatted into it, its schema, and the user-message
    templates around the user's words, rendered from placeholders."""
    from app import entity_grounding
    from app.agents import nodes, state
    from app.routers import chat

    dated_message = nodes._dated_message("<query>", _SAMPLE_DAY)
    return {
        "supervisor": (nodes._SUPERVISOR_SYSTEM_PROMPT, nodes._SUPERVISOR_SCHEMA),
        "grounding_gate": (nodes._GENERAL_GROUNDING_GATE_PROMPT, nodes._GROUNDING_GATE_SCHEMA),
        "research_tool": (
            nodes._RESEARCH_TOOL_SYSTEM_PROMPT,
            nodes._RESEARCH_TOOL_SCHEMA,
            nodes.RESEARCH_SCRATCHPAD,
        ),
        "action_target": (
            nodes.ACTION_TARGET_SYSTEM_PROMPT,
            nodes.action_target_message("<query>", [_SAMPLE_ENTITY]),
            nodes.action_target_schema(["<id>"]),
        ),
        "log_fields": (
            nodes.LOG_FIELDS_SYSTEM_PROMPT,
            nodes.LOG_TYPE_DESCRIPTIONS,
            nodes.log_fields_schema(("<type>",)),
            dated_message,
        ),
        "schedule_kind": (
            nodes.SCHEDULE_KIND_SYSTEM_PROMPT,
            nodes.INTERVAL_TYPE_DESCRIPTIONS,
            nodes.SCHEDULE_KIND_SCHEMA,
        ),
        "schedule_detail": (
            nodes.SCHEDULE_DETAIL_SYSTEM_PROMPTS,
            nodes.SCHEDULE_DETAIL_SCHEMAS,
            nodes._DATED_INTERVAL_TYPES,
            dated_message,
        ),
        "last_done_date": (nodes.LAST_DONE_SYSTEM_PROMPT, nodes.LAST_DONE_SCHEMA, dated_message),
        "fuzzy_entity_match": (
            entity_grounding._FUZZY_ENTITY_MATCH_SYSTEM_PROMPT,
            entity_grounding.fuzzy_entity_match_schema(["<id>"]),
            entity_grounding.fuzzy_match_message("<query>", [_SAMPLE_ENTITY]),
        ),
        "personas": (
            state.BASE_SYSTEM_PROMPT,
            state.MAINTENANCE_PERSONA,
            state.VEHICLE_PERSONA,
            state.RESEARCH_PERSONA,
            state.ACTION_PERSONA,
        ),
        "answer_context": (
            chat.NO_CONTEXT_SUFFIX,
            chat.NO_DOCUMENTS_NOTE,
            chat.NO_ENTITY_NOTE,
            chat.NO_HISTORY_NOTE,
            chat.CONTEXT_INSTRUCTIONS,
            chat.ENTITY_CONTEXT_INSTRUCTIONS,
            chat.WEB_RESULTS_INSTRUCTIONS,
        ),
    }


def prompt_fingerprints() -> dict[str, str]:
    return {
        name: hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]
        for name, parts in prompt_contents().items()
    }
