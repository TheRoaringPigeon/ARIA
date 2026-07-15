from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_SERVICE_")

    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    core_api_url: str = "http://core-api:8000"
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "qwen3:14b"


settings = Settings()
