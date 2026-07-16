from typing import ClassVar

from aria_shared.types import MongoBaseModel


class BaseAttributes(MongoBaseModel):
    DOMAIN: ClassVar[str]
    VALID_STATUSES: ClassVar[tuple[str, ...]]
    LOG_TYPES: ClassVar[tuple[str, ...]]
