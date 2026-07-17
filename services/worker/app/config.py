from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_")

    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/1"

    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db_name: str = "aria"

    # Same S3 client shape as core-api/app/s3.py — MinIO locally
    # (endpoint_url set), real AWS S3 in production (unset).
    s3_endpoint_url: str | None = None
    s3_bucket: str = "aria-documents"
    s3_access_key_id: str = "aria"
    s3_secret_access_key: str = "aria-dev-secret"
    s3_region: str = "us-east-1"

    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "qwen3:14b"
    # Separate from `ollama_model` — must match `AI_SERVICE_EMBED_MODEL` in
    # ai-service, since chunks embedded here are queried there against the
    # same Chroma collection.
    embed_model: str = "nomic-embed-text"


settings = Settings()
