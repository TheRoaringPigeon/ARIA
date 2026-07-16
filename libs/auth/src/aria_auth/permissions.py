from typing import Literal, get_args

from fastapi import HTTPException, status

from aria_shared.models import EntityDomain, Role

Action = Literal["create", "update", "archive", "restore", "delete"]

ALL_ROLES: frozenset[Role] = frozenset(get_args(Role))

# (domain, action) -> roles allowed to perform it. `domain=None` is a
# fallback that applies across every domain. Empty today — single-household,
# trusted-user use case, no product requirement yet says who can do what
# (see docs/scaling-debt.md #5) — but every mutating route already calls
# check_permission(), so adding a real restriction later is one line here,
# not a new `if session.role != "owner"` threaded into a router by hand.
PERMISSIONS: dict[tuple[EntityDomain | None, Action], frozenset[Role]] = {}


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
