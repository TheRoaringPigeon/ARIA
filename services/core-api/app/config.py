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

    # S3-compatible object storage for document uploads (M2). MinIO locally
    # (endpoint_url set), real AWS S3 in production (endpoint_url unset —
    # boto3's virtual-hosted-style default applies). See docs/plans/m2-document-ingestion-hub.md.
    s3_endpoint_url: str | None = None
    s3_bucket: str = "aria-documents"
    s3_access_key_id: str = "aria"
    s3_secret_access_key: str = "aria-dev-secret"
    s3_region: str = "us-east-1"
    max_upload_bytes: int = 25 * 1024 * 1024

    # Standalone Celery producer (no result backend) — enqueues
    # process_document tasks for `worker` without core-api importing
    # worker's Celery app.
    celery_broker_url: str = "redis://redis:6379/0"


settings = Settings()
