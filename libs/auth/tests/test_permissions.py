import pytest
from fastapi import HTTPException

from aria_auth.permissions import PERMISSIONS, allowed_roles, check_permission


@pytest.fixture(autouse=True)
def clean_registry():
    """PERMISSIONS is a module-level mutable dict — clear it around every
    test so one test's registry entry can't leak into the next.
    """
    PERMISSIONS.clear()
    yield
    PERMISSIONS.clear()


def test_permissive_by_default():
    assert allowed_roles("vehicle", "delete") == {"owner", "member"}
    check_permission("member", "vehicle", "delete")  # does not raise


def test_domain_specific_entry_restricts():
    PERMISSIONS[("vehicle", "archive")] = frozenset({"owner"})
    check_permission("owner", "vehicle", "archive")
    with pytest.raises(HTTPException) as exc_info:
        check_permission("member", "vehicle", "archive")
    assert exc_info.value.status_code == 403


def test_domain_specific_entry_does_not_leak_to_other_domains():
    PERMISSIONS[("vehicle", "archive")] = frozenset({"owner"})
    check_permission("member", "person", "archive")  # unaffected, no raise


def test_wildcard_entry_applies_across_domains():
    PERMISSIONS[(None, "delete")] = frozenset({"owner"})
    with pytest.raises(HTTPException):
        check_permission("member", "vehicle", "delete")
    with pytest.raises(HTTPException):
        check_permission("member", "person", "delete")


def test_domain_specific_entry_overrides_wildcard():
    PERMISSIONS[(None, "delete")] = frozenset({"owner"})
    PERMISSIONS[("vehicle", "delete")] = frozenset({"owner", "member"})
    check_permission("member", "vehicle", "delete")  # domain-specific wins
    with pytest.raises(HTTPException):
        check_permission("member", "person", "delete")  # falls back to wildcard
