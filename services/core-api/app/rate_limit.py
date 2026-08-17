import logging

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import get_client

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """Prefer the first X-Forwarded-For entry when present, else the
    directly-connected socket's IP. Safe to trust unconditionally in this
    app's topology: in prod (docker-compose.prod.yml + Caddyfile), core-api
    has no published port and is reachable *only* via Caddy's
    `reverse_proxy core-api:8000`, which sets X-Forwarded-For on every
    request it proxies — nothing else can reach core-api directly to spoof
    it. In dev (docker-compose.yml), core-api's port is published with no
    proxy in front at all, so there's no header to spoof there either;
    request.client.host is already the real caller in that case, which is
    exactly what this falls back to.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _retry_after_if_limited(key: str, limit: int) -> int | None:
    """Read-only fixed-window check — does NOT increment. Returns the key's
    remaining TTL in seconds (>=1) if it's already at/over `limit`, else
    None. Fails open (returns None, i.e. "not limited") on any Redis error,
    logging a warning — a rate-limit infra outage must never block login/
    signup, matching app/celery_client.py's existing fail-open stance for a
    secondary system's outage not blocking a primary write path.
    """
    try:
        client = get_client()
        current = await client.get(key)
        if current is None or int(current) < limit:
            return None
        ttl = await client.ttl(key)
        return ttl if ttl and ttl > 0 else 1
    except RedisError:
        logger.warning("rate limit check failed, failing open for key=%s", key, exc_info=True)
        return None


async def _record_attempt(key: str, window_seconds: int) -> None:
    """Fixed-window counter: INCR, then EXPIRE only on the increment that
    created the key (count == 1) — adequate for abuse resistance, not
    billing precision. A missed increment (Redis unreachable) just means one
    fewer counted attempt; it never blocks the request — same fail-open
    stance as the check above.
    """
    try:
        client = get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
    except RedisError:
        logger.warning("rate limit increment failed, not counted for key=%s", key, exc_info=True)


def _raise_429(retry_after: int, detail: str) -> None:
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail,
        headers={"Retry-After": str(retry_after)},
    )


async def check_login_rate_limit(request: Request, email: str) -> None:
    """Call at the top of POST /auth/login, before touching Mongo. Checks
    both the caller's IP and the target email's failed-attempt counters —
    either being at/over its limit is a 429, checked before any DB work.
    """
    ip_key = f"ratelimit:login:ip:{get_client_ip(request)}"
    email_key = f"ratelimit:login:email:{email.strip().lower()}"
    retry_after = await _retry_after_if_limited(ip_key, settings.login_rate_limit_attempts)
    if retry_after is None:
        retry_after = await _retry_after_if_limited(email_key, settings.login_rate_limit_attempts)
    if retry_after is not None:
        _raise_429(retry_after, "too many login attempts, try again later")


async def record_login_failure(request: Request, email: str) -> None:
    """Call only on the login route's own 401 branch — never on success and
    never on request volume alone, so a household sharing one IP or a user
    who mistypes a password once then succeeds is never throttled by
    legitimate use.
    """
    ip_key = f"ratelimit:login:ip:{get_client_ip(request)}"
    email_key = f"ratelimit:login:email:{email.strip().lower()}"
    await _record_attempt(ip_key, settings.login_rate_limit_window_seconds)
    await _record_attempt(email_key, settings.login_rate_limit_window_seconds)


async def check_and_record_signup_attempt(request: Request) -> None:
    """Call at the top of POST /auth/signup, before touching Mongo. Unlike
    login, this counts every attempt regardless of outcome (a duplicate-
    email 409 still consumes quota) — the abuse being guarded against is
    household-creation *volume*, not credential guessing, so check and
    record happen together here rather than being split like login's.
    """
    ip_key = f"ratelimit:signup:ip:{get_client_ip(request)}"
    retry_after = await _retry_after_if_limited(ip_key, settings.signup_rate_limit_attempts)
    if retry_after is not None:
        _raise_429(retry_after, "too many signup attempts, try again later")
    await _record_attempt(ip_key, settings.signup_rate_limit_window_seconds)
