from aria_auth import SessionContext, has_shared_access

_OWNER = SessionContext(household_id="h1", user_id="owner-1", user_name="Owner", role="owner")
_CREATOR = SessionContext(household_id="h1", user_id="creator-1", user_name="Creator", role="member")
_OTHER_MEMBER = SessionContext(household_id="h1", user_id="other-1", user_name="Other", role="member")


def test_owner_always_has_access_regardless_of_shared_with():
    assert has_shared_access(_OWNER, [], "creator-1")
    assert has_shared_access(_OWNER, "household", "creator-1")


def test_creator_always_has_access_even_when_excluded_from_shared_with():
    assert has_shared_access(_CREATOR, [], "creator-1")
    assert has_shared_access(_CREATOR, ["someone-else"], "creator-1")


def test_household_grants_any_member():
    assert has_shared_access(_OTHER_MEMBER, "household", "creator-1")


def test_member_not_listed_and_not_creator_denied():
    assert not has_shared_access(_OTHER_MEMBER, ["creator-1"], "creator-1")
    assert not has_shared_access(_OTHER_MEMBER, [], "creator-1")


def test_member_listed_in_shared_with_granted():
    assert has_shared_access(_OTHER_MEMBER, ["other-1"], "creator-1")
