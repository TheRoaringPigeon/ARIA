from datetime import date
from typing import ClassVar, Literal

from .base import BaseAttributes


class EquipmentAttrs(BaseAttributes):
    DOMAIN: ClassVar[str] = "equipment"
    VALID_STATUSES: ClassVar[set[str]] = {"active", "in_service", "retired"}
    LOG_TYPES: ClassVar[set[str]] = {"service", "repair", "inspection", "expense", "note", "milestone"}

    domain: Literal["equipment"] = "equipment"
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    purchase_date: date | None = None
