import hashlib
import hmac
import secrets

# OWASP's current minimum recommendation for PBKDF2-HMAC-SHA256. Stdlib
# hashlib.pbkdf2_hmac is a deliberate choice over adding bcrypt/passlib/
# argon2 as a new dependency — none exists anywhere in this workspace today,
# and PBKDF2-HMAC-SHA256 at this iteration count is a legitimate,
# dependency-free way to hash passwords.
_ITERATIONS = 600_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${derived.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verification against a hash_password()-produced string.

    Returns False (never raises) on a malformed or missing `stored` value —
    a corrupted/foreign hash, or a pre-migration user document that
    predates this field entirely (real case hit live: a dev household
    seeded before M9 shipped), should fail the login, not 500 the request.
    """
    try:
        algorithm, iterations_str, salt, expected_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False

    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(derived.hex(), expected_hex)
