from datetime import date
from typing import ClassVar, Literal

from .base import BaseAttributes


class HomeAttrs(BaseAttributes):
    DOMAIN: ClassVar[str] = "home"
    VALID_STATUSES: ClassVar[tuple[str, ...]] = ("active", "needs_attention", "archived")
    LOG_TYPES: ClassVar[tuple[str, ...]] = ("service", "repair", "inspection", "expense", "note", "milestone")

    domain: Literal["home"] = "home"
    entity_type: Literal["room", "system", "appliance", "structure"]

    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    paint_brand: str | None = None
    paint_code: str | None = None
    install_date: date | None = None
    warranty_expires_at: date | None = None
