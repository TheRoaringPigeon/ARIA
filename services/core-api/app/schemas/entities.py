from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from aria_shared.models.entities import EntityAttributes, EntityDomain, SharedWith

# tags/specs are a deliberate free-form "pressure-release valve" (see
# docs/data-model.md) with no domain-level shape to validate against — these
# caps exist purely to bound the worst-case per-document cost of
# entities.py's `_search_filter` regex/$objectToArray scan
# (scaling-debt.md #7), not because a larger value is meaningfully wrong.
_NAME_MAX_LENGTH = 200
_LOCATION_MAX_LENGTH = 200
_TAG_MAX_LENGTH = 100
_TAGS_MAX_COUNT = 50
_SPECS_KEY_MAX_LENGTH = 200
_SPECS_VALUE_MAX_LENGTH = 200
_SPECS_MAX_COUNT = 50

_Tag = Annotated[str, Field(max_length=_TAG_MAX_LENGTH)]
_SpecKey = Annotated[str, Field(max_length=_SPECS_KEY_MAX_LENGTH)]
_SpecValue = Annotated[str, Field(max_length=_SPECS_VALUE_MAX_LENGTH)]
_Tags = Annotated[list[_Tag], Field(max_length=_TAGS_MAX_COUNT)]
_Specs = Annotated[dict[_SpecKey, _SpecValue], Field(max_length=_SPECS_MAX_COUNT)]


class EntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: EntityDomain
    name: str = Field(max_length=_NAME_MAX_LENGTH)
    status: str
    tags: _Tags = []
    location: str | None = Field(default=None, max_length=_LOCATION_MAX_LENGTH)
    specs: _Specs = {}
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

    name: str | None = Field(default=None, max_length=_NAME_MAX_LENGTH)
    status: str | None = None
    tags: _Tags | None = None
    location: str | None = Field(default=None, max_length=_LOCATION_MAX_LENGTH)
    specs: _Specs | None = None
    shared_with: SharedWith | None = None
    attributes: EntityAttributes | None = None
