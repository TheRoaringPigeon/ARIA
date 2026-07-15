from pydantic import BaseModel, ConfigDict

from aria_shared.models.entities import EntityAttributes, EntityDomain


class EntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: EntityDomain
    name: str
    status: str
    tags: list[str] = []
    location: str | None = None
    specs: dict[str, str] = {}
    attributes: EntityAttributes


class EntityUpdate(BaseModel):
    """Partial update. household_id/domain/archived_at are deliberately not
    fields here — archival goes through the dedicated archive/restore
    endpoints, and domain is immutable once an entity is created.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    location: str | None = None
    specs: dict[str, str] | None = None
    attributes: EntityAttributes | None = None
