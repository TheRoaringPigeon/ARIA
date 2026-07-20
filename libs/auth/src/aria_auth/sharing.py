from aria_auth.session import SessionContext


def has_shared_access(
    session: SessionContext, shared_with: str | list[str], owner_user_id: str
) -> bool:
    """Whether `session`'s caller may view/edit a record with this
    `shared_with` setting and `owner_user_id` (the record's creator/
    uploader — models name this field differently, hence the primitive
    parameters rather than a raw Mongo doc).

    The household owner and the record's own creator always have access,
    regardless of `shared_with` — a creator who later narrows sharing to
    exclude themselves can't accidentally lock themselves out, and the
    owner's access never depends on being explicitly listed anywhere.
    """
    if session.role == "owner":
        return True
    if session.user_id == owner_user_id:
        return True
    if shared_with == "household":
        return True
    return session.user_id in shared_with
