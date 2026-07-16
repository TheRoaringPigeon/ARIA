from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from aria_shared.types import MongoBaseModel, PyObjectId

from .base import BaseAttributes
from .equipment import EquipmentAttrs
from .home import HomeAttrs
from .person import PersonAttrs
from .project import ProjectAttrs
from .vehicle import VehicleAttrs

# Adding a domain: create <domain>.py (a BaseAttributes subclass with
# DOMAIN/VALID_STATUSES/LOG_TYPES ClassVars + fields), then add it to the
# three spots below.
EntityDomain = Literal["home", "vehicle", "equipment", "project", "person"]

EntityAttributes = Annotated[
    Union[HomeAttrs, VehicleAttrs, EquipmentAttrs, ProjectAttrs, PersonAttrs],
    Field(discriminator="domain"),
]

ENTITY_DOMAINS: dict[str, type[BaseAttributes]] = {
    cls.DOMAIN: cls for cls in (HomeAttrs, VehicleAttrs, EquipmentAttrs, ProjectAttrs, PersonAttrs)
}


class EntityBase(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    domain: EntityDomain
    name: str
    status: str
    tags: list[str] = []
    location: str | None = None
    specs: dict[str, str] = {}
    created_by: PyObjectId
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    attributes: EntityAttributes

    @model_validator(mode="after")
    def _check_domain_consistency(self) -> "EntityBase":
        if self.domain != self.attributes.domain:
            raise ValueError(
                f"domain {self.domain!r} does not match attributes.domain {self.attributes.domain!r}"
            )
        valid_statuses = self.attributes.VALID_STATUSES
        if self.status not in valid_statuses:
            raise ValueError(
                f"status {self.status!r} is not valid for domain {self.domain!r}; "
                f"expected one of {sorted(valid_statuses)}"
            )
        return self
