from datetime import date
from typing import ClassVar, Literal

from .base import BaseAttributes


class PersonAttrs(BaseAttributes):
    DOMAIN: ClassVar[str] = "person"
    VALID_STATUSES: ClassVar[set[str]] = {"active", "inactive"}
    LOG_TYPES: ClassVar[set[str]] = {"conversation", "call", "meeting", "gift", "milestone"}

    domain: Literal["person"] = "person"
    relationship: str | None = None  # "friend", "family", "colleague", "neighbor", ... — free text
    company: str | None = None
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    birthday: date | None = None
