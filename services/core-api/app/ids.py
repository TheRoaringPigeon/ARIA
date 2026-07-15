from bson import ObjectId


def new_id() -> str:
    """Generate a fresh id in the same shape Mongo would assign.

    Every collection in this codebase stores and queries ids as plain
    strings (see aria_shared.types.PyObjectId — it always coerces to str),
    not as bson.ObjectId, so writes generate the id up front rather than
    relying on Mongo's driver-side auto-assignment.
    """
    return str(ObjectId())
