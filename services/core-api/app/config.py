from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORE_API_")

    # "development" (default, used by docker-compose.yml) or "production"
    # (set explicitly by docker-compose.prod.yml) — not used for any
    # behavioral branching beyond the admin_password fail-fast check below.
    environment: str = "development"

    # replicaSet=rs0 — Mongo runs as a single-node replica set (see
    # docker-compose.yml's mongo/mongo-init services), not a bare
    # standalone, so multi-document transactions (app/db.py's
    # start_transaction()) are available.
    mongo_uri: str = "mongodb://mongo:27017/?replicaSet=rs0"
    mongo_db_name: str = "aria"

    frontend_origin: str = "http://localhost:5173"
    # Set true in prod (docker-compose.prod.yml), where the stack is only
    # ever reachable over HTTPS via Caddy — the session cookie should never
    # be sent in cleartext. False by default so dev/test (plain
    # http://localhost, no TLS) keeps working: a Secure cookie set over an
    # insecure response is silently refused by the browser.
    cookie_secure: bool = False
    # Whether to run the one-time seed-household bootstrap on this startup
    # at all (app/seed.py's ensure_seed_household, called from main.py's
    # lifespan). Off by default — a real deployment only ever needs this on
    # its very first boot, before any household exists; every boot after
    # that for the rest of the deployment's life should skip it entirely
    # rather than pay a no-op idempotency check forever. docker-compose.yml
    # (dev) sets this true so `docker compose up` keeps seeding
    # owner@household.local out of the box; docker-compose.prod.yml leaves
    # it off, and an operator sets CORE_API_SEED_ADMIN=true for exactly the
    # one deploy that needs to create the first owner login.
    seed_admin: bool = False
    # The seeded owner's *initial* password (see app/seed.py) — only
    # consulted while seed_admin is true. No longer a bypass that logs in as
    # that user regardless of identity: real login is per-user email+
    # password (app/routers/auth.py); this only seeds the one dev/test
    # household's owner account with a known starting credential.
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

    # Page-size cap shared by every offset/limit list endpoint (entities,
    # logs, documents) — one place so raising or lowering it doesn't mean
    # hunting down multiple copies of the same literal.
    max_page_limit: int = 200

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

    # Rate limiting on /auth/login and /auth/signup (app/rate_limit.py) —
    # abuse resistance (docs/scaling-debt.md #10). Same physical Redis
    # instance as the Celery broker/result backend above, just a different
    # logical db index (0=broker, 1=worker's result backend, 2=here) so an
    # incident responder can `redis-cli -n 2 KEYS 'ratelimit:*'` and see only
    # rate-limit keys — key names are already `ratelimit:`-prefixed, so this
    # isolation is convenience/hygiene, not a correctness requirement.
    rate_limit_redis_url: str = "redis://redis:6379/2"

    # Login: two independent fixed-window failed-attempt counters (per
    # caller IP and per target email), incremented only on a failed login —
    # never on success, never on request volume alone — so a household
    # sharing one IP, or a user who mistypes a password once then succeeds,
    # is never throttled by legitimate use. 10 attempts / 15 minutes is slow
    # enough to make online credential stuffing impractical while staying
    # well above legitimate fumbling.
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 15 * 60

    # Signup: one per-IP request-volume counter, incremented on every
    # attempt regardless of outcome (a duplicate-email 409 still consumes
    # quota) — the abuse this guards against is household-creation *volume*,
    # not credential guessing. A real household signs up once, ever; 5/hour
    # per IP blocks a scripted spam loop while staying far above any
    # plausible legitimate rate.
    signup_rate_limit_attempts: int = 5
    signup_rate_limit_window_seconds: int = 60 * 60

    @model_validator(mode="after")
    def _require_real_admin_password_outside_dev(self) -> "Settings":
        # Only matters when seeding is actually going to run (seed_admin) —
        # once a deployment's bootstrap deploy is done and the flag is
        # turned back off, admin_password is never read at all, so there's
        # nothing to validate. When it does run, still catches both a
        # deploy that forgets CORE_API_ADMIN_PASSWORD entirely (falls back
        # to the "aria-dev" default) and .env.prod being left with its
        # shipped-blank value (empty string, which pydantic-settings treats
        # as "set" — no fallback to the default).
        if (
            self.seed_admin
            and self.environment != "development"
            and (not self.admin_password or self.admin_password == "aria-dev")
        ):
            raise ValueError(
                "CORE_API_ADMIN_PASSWORD must be set to a real value "
                f"when CORE_API_ENVIRONMENT={self.environment!r} and "
                "CORE_API_SEED_ADMIN=true — refusing to seed the owner "
                "account with an empty or default dev password."
            )
        return self


settings = Settings()
