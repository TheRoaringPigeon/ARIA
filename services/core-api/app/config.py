from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORE_API_")

    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db_name: str = "aria"


settings = Settings()
