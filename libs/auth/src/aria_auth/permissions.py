from typing import Literal, get_args

from fastapi import HTTPException, status

from aria_shared.models import EntityDomain, Role

Action = Literal["create", "update", "archive", "restore", "delete"]

ALL_ROLES: frozenset[Role] = frozenset(get_args(Role))

# (domain, action) -> roles allowed to perform it. `domain=None` is a
# fallback that applies across every domain.
#
# Hard delete is owner-only, regardless of domain — a household's
# hard-delete risk (irreversible, unlike archive-in-place) is the same no
# matter which domain the record belongs to, so this is the `(None, ...)`
# wildcard rather than five duplicated per-domain entries. Create/update/
# archive/restore stay open to any household member (governed by sharing,
# see aria_auth.sharing, not by role) — a household's members are trusted
# co-residents, and archive-in-place is already the low-stakes reversible
# path. More granular role rules can be added here later without touching
# a router — every mutating route already calls check_permission().
PERMISSIONS: dict[tuple[EntityDomain | None, Action], frozenset[Role]] = {
    (None, "delete"): frozenset({"owner"}),
}


def allowed_roles(domain: EntityDomain | None, action: Action) -> frozenset[Role]:
    if (domain, action) in PERMISSIONS:
        return PERMISSIONS[(domain, action)]
    if (None, action) in PERMISSIONS:
        return PERMISSIONS[(None, action)]
    return ALL_ROLES


def check_permission(role: Role, domain: EntityDomain | None, action: Action) -> None:
    if role not in allowed_roles(domain, action):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"role {role!r} may not {action} {domain or 'this resource'}"
        )
