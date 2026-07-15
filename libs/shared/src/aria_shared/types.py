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


class MongoBaseModel(BaseModel):
    """Base for every document stored in Mongo.

    populate_by_name lets us declare the field as `id` in Python while
    reading/writing Mongo's `_id` on the wire.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    def to_mongo(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)
