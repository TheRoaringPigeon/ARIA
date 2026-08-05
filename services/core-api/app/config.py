from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORE_API_")

    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db_name: str = "aria"

    frontend_origin: str = "http://localhost:5173"
    # Set true in prod (docker-compose.prod.yml), where the stack is only
    # ever reachable over HTTPS via Caddy — the session cookie should never
    # be sent in cleartext. False by default so dev/test (plain
    # http://localhost, no TLS) keeps working: a Secure cookie set over an
    # insecure response is silently refused by the browser.
    cookie_secure: bool = False
    # The seeded owner's *initial* password (see app/seed.py) — no longer a
    # bypass that logs in as that user regardless of identity. Real login is
    # per-user email+password (app/routers/auth.py); this only seeds the one
    # dev/test household's owner account with a known starting credential.
    admin_password: str = "aria-dev"
    session_ttl_hours: int = 24 * 7
    invite_ttl_hours: int = 24 * 7

    # Seed household/user — created on startup if it doesn't already exist.
    # See app/seed.py. Real multi-household signup (app/routers/auth.py's
    # POST /auth/signup) is the general path; this is just dev/test's first
    # household so there's always something to log into out of the box.
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

    # How long an in-progress mobile photo-capture draft (document_drafts,
    # M12) survives without activity before purge_expired_upload_drafts
    # removes it. Keyed off last_activity_at, not created_at, so a draft
    # actively being worked doesn't expire mid-capture even if the capture
    # session spans multiple days. Matches the session-cookie TTL
    # convention (168h = 7 days). Duplicated in worker/app/config.py, same
    # pattern as entity_trash_grace_hours.
    upload_draft_ttl_hours: int = 168

    # Standalone Celery producer (no result backend) — enqueues
    # process_document tasks for `worker` without core-api importing
    # worker's Celery app.
    celery_broker_url: str = "redis://redis:6379/0"


settings = Settings()
