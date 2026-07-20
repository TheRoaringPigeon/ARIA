from pydantic import BaseModel, ConfigDict

from aria_shared.models.entities import EntityAttributes, EntityDomain, SharedWith


class EntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: EntityDomain
    name: str
    status: str
    tags: list[str] = []
    location: str | None = None
    specs: dict[str, str] = {}
    shared_with: SharedWith = "household"
    attributes: EntityAttributes


class EntityUpdate(BaseModel):
    """Partial update. household_id/domain/archived_at are deliberately not
    fields here — archival goes through the dedicated archive/restore
    endpoints, and domain is immutable once an entity is created.

    `shared_with` is `None` by default here (unlike `EntityCreate`, where
    it defaults to `"household"`) specifically so `"shared_with" in
    body.model_fields_set` can distinguish "not touching sharing" from "set
    it to household" — both would otherwise be indistinguishable from the
    field's own default.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    location: str | None = None
    specs: dict[str, str] | None = None
    shared_with: SharedWith | None = None
    attributes: EntityAttributes | None = None
