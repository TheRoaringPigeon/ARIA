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

    # Outbound SMTP for the overdue-items email digest
    # (app/tasks/send_overdue_digest.py). Dev points at the `mailpit`
    # compose service (no auth/TLS needed); real relay creds are sourced
    # from prod secrets, mirroring how S3/mongo creds are sourced there.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_from_address: str = "aria@household.local"

    # Used to build the "open ARIA" link in the digest email body.
    frontend_origin: str = "http://localhost:5173"

    # Overrides the digest's daily crontab with a fixed-second interval —
    # set only in dev's docker-compose.yml (worker-beat) so digests are
    # always visibly repeatable in Mailpit without touching code. Unset
    # (None) in prod, which keeps the shipped daily schedule.
    overdue_digest_interval_seconds: int | None = None


settings = Settings()
