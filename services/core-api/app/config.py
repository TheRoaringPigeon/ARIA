from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORE_API_")

    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db_name: str = "aria"

    frontend_origin: str = "http://localhost:5173"
    admin_password: str = "aria-dev"
    session_ttl_hours: int = 24 * 7

    # Seed household/user — M1 supports exactly one household, created on
    # startup if it doesn't already exist. See app/seed.py.
    seed_household_name: str = "My Household"
    seed_user_name: str = "Owner"
    seed_user_email: str = "owner@household.local"


settings = Settings()
