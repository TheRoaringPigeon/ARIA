import pytest
from pydantic import ValidationError

from app.config import Settings


def test_seed_admin_defaults_off():
    assert Settings().seed_admin is False


def test_dev_default_admin_password_is_fine_in_development():
    settings = Settings(environment="development", seed_admin=True)
    assert settings.admin_password == "aria-dev"


def test_production_rejects_default_admin_password_while_seeding():
    with pytest.raises(ValidationError, match="CORE_API_ADMIN_PASSWORD"):
        Settings(environment="production", seed_admin=True, admin_password="aria-dev")


def test_production_rejects_blank_admin_password_while_seeding():
    with pytest.raises(ValidationError, match="CORE_API_ADMIN_PASSWORD"):
        Settings(environment="production", seed_admin=True, admin_password="")


def test_production_accepts_a_real_admin_password_while_seeding():
    settings = Settings(environment="production", seed_admin=True, admin_password="a-real-secret")
    assert settings.admin_password == "a-real-secret"


def test_production_with_seeding_off_does_not_require_a_real_password():
    """The steady-state case: seed_admin=False (the default) means
    ensure_seed_household never runs, so a blank/default admin_password
    isn't a live insecure credential — nothing to fail-fast on.
    """
    settings = Settings(environment="production", seed_admin=False, admin_password="")
    assert settings.admin_password == ""
