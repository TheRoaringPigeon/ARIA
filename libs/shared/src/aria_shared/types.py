from datetime import date, datetime, time, timezone
from typing import Annotated, Any

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

# Mongo's _id is a bson.ObjectId at rest. Pydantic doesn't know that type,
# so on the way in we accept either an ObjectId or an already-stringified id
# and coerce to str; on the way out we always serialize as str.
PyObjectId = Annotated[
    str,
    BeforeValidator(lambda v: str(v) if isinstance(v, ObjectId) else v),
    PlainSerializer(lambda v: str(v), return_type=str),
]


def _encode_dates_for_bson(value: Any) -> Any:
    """BSON has no native `date` type, only `datetime` — inserting a bare
    `datetime.date` (e.g. LogEntry.occurred_at, Schedule.next_due_at,
    VehicleAttrs.purchase_date) raises InvalidDocument. Recursively convert
    any bare `date` into a UTC-midnight `datetime` before writing; reading
    back relies on Pydantic's date validator, which accepts an
    exact-midnight `datetime` and truncates it to a `date`.
    """
    if isinstance(value, dict):
        return {k: _encode_dates_for_bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode_dates_for_bson(v) for v in value]
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return value


class MongoBaseModel(BaseModel):
    """Base for every document stored in Mongo.

    populate_by_name lets us declare the field as `id` in Python while
    reading/writing Mongo's `_id` on the wire.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    def to_mongo(self) -> dict[str, Any]:
        return _encode_dates_for_bson(self.model_dump(by_alias=True))
