from aria_auth import hash_password, verify_password


def test_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_wrong_password_rejected():
    stored = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", stored)


def test_malformed_stored_hash_rejected_not_raises():
    assert not verify_password("anything", "not-a-real-hash")
    assert not verify_password("anything", "pbkdf2_sha256$not-an-int$abcd$abcd")


def test_none_stored_hash_rejected_not_raises():
    """A user document from before `password_hash` existed at all (real
    case hit live against the persistent dev stack: a household seeded
    pre-M9) has no such field — `.get("password_hash")` resolves to
    `None`, which must fail the login cleanly rather than raise.
    """
    assert not verify_password("anything", None)


def test_unknown_algorithm_rejected():
    assert not verify_password("anything", "bcrypt$12$salt$hash")


def test_hash_is_salted():
    assert hash_password("same password") != hash_password("same password")
