LOG_OIL_CHANGE = {
    "target": {"reason": "the user did an oil change", "tool": "create_log", "entity_id": "e1"},
    "log_fields": {
        "type": "service",
        "occurred_at": "2026-07-18",
        "title": "Oil change",
        "description": None,
        "cost": None,
        "metrics": {},
    },
}
OIL_CHANGE_ARGS = {
    "entity_id": "e1",
    "type": "service",
    "occurred_at": "2026-07-18",
    "title": "Oil change",
}
OIL_CHANGE_SUMMARY = "Log service: Oil change for Ranger, 2026-07-18"


def reply_for(schema: dict, replies: dict) -> dict:
    """Answers a write-path model call as `replies` says, picking the step
    by the schema the call was given."""
    properties = schema["properties"]
    if "agent" in properties:
        return {"reason": "a request to record something", "agent": "action"}
    if "tool" in properties:
        return replies["target"]
    if "occurred_at" in properties:
        return replies["log_fields"]
    if "interval_type" in properties:
        return replies["schedule_kind"]
    return replies["schedule_detail"]
