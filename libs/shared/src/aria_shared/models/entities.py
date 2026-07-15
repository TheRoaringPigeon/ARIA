from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from aria_shared.types import MongoBaseModel, PyObjectId

EntityDomain = Literal["home", "vehicle", "equipment", "project"]

# Per-domain status vocab, transcribed from data-model.md §3. Not modeled as
# a Literal per domain (would need a discriminated status type alongside
# `attributes`) — a validator is the lighter-weight enforcement for v0.
STATUS_BY_DOMAIN: dict[EntityDomain, set[str]] = {
    "home": {"active", "needs_attention", "archived"},
    "vehicle": {"active", "in_service", "sold", "archived"},
    "equipment": {"active", "in_service", "retired"},
    "project": {"planning", "in_progress", "on_hold", "completed"},
}


class HomeAttrs(MongoBaseModel):
    domain: Literal["home"] = "home"
    entity_type: Literal["room", "system", "appliance", "structure"]

    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    paint_brand: str | None = None
    paint_code: str | None = None
    install_date: date | None = None
    warranty_expires_at: date | None = None


class VehicleAttrs(MongoBaseModel):
    domain: Literal["vehicle"] = "vehicle"
    make: str
    model: str
    year: int
    vin: str | None = None
    license_plate: str | None = None
    current_mileage: int | None = None
    purchase_date: date | None = None


class EquipmentAttrs(MongoBaseModel):
    domain: Literal["equipment"] = "equipment"
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    purchase_date: date | None = None


class ProjectAttrs(MongoBaseModel):
    domain: Literal["project"] = "project"
    related_entity_ids: list[PyObjectId] = []
    start_date: date | None = None
    target_end_date: date | None = None
    completed_date: date | None = None
    budget_estimate: float | None = None


EntityAttributes = Annotated[
    Union[HomeAttrs, VehicleAttrs, EquipmentAttrs, ProjectAttrs],
    Field(discriminator="domain"),
]


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
        valid_statuses = STATUS_BY_DOMAIN[self.domain]
        if self.status not in valid_statuses:
            raise ValueError(
                f"status {self.status!r} is not valid for domain {self.domain!r}; "
                f"expected one of {sorted(valid_statuses)}"
            )
        return self
