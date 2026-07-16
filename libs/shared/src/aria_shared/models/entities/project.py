from datetime import date
from typing import ClassVar, Literal

from aria_shared.types import PyObjectId

from .base import BaseAttributes


class ProjectAttrs(BaseAttributes):
    DOMAIN: ClassVar[str] = "project"
    VALID_STATUSES: ClassVar[set[str]] = {"planning", "in_progress", "on_hold", "completed"}
    LOG_TYPES: ClassVar[set[str]] = {"service", "repair", "inspection", "expense", "note", "milestone"}

    domain: Literal["project"] = "project"
    related_entity_ids: list[PyObjectId] = []
    start_date: date | None = None
    target_end_date: date | None = None
    completed_date: date | None = None
    budget_estimate: float | None = None
