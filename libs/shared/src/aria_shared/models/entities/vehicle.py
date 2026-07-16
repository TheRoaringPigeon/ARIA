from datetime import date
from typing import ClassVar, Literal

from .base import BaseAttributes


class VehicleAttrs(BaseAttributes):
    DOMAIN: ClassVar[str] = "vehicle"
    VALID_STATUSES: ClassVar[tuple[str, ...]] = ("active", "in_service", "sold", "archived")
    LOG_TYPES: ClassVar[tuple[str, ...]] = ("service", "repair", "inspection", "expense", "note", "milestone")

    domain: Literal["vehicle"] = "vehicle"
    make: str
    model: str
    year: int
    vin: str | None = None
    license_plate: str | None = None
    current_mileage: int | None = None
    purchase_date: date | None = None
