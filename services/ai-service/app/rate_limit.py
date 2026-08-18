import logging

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import get_client

logger = logging.getLogger(__name__)


async def _retry_after_if_limited(key: str, limit: int, window_seconds: int) -> int | None:
    """Atomic fixed-window check-and-increment. INCR is atomic in Redis and
    returns the post-increment count, so concurrent callers each get a
    distinct number — unlike a separate check-then-increment (GET, decide,
    then INCR), where two concurrent requests could both read a count under
    `limit` and both pass before either incremented. EXPIRE is only set on
    the increment that created the key (count == 1). Returns the key's
    remaining TTL in seconds (>=1) if this call's increment pushed the count
    over `limit`, else None. Fails open (returns None, i.e. "not limited")
    on any Redis error, logging a warning — a rate-limit infra outage must
    never block /chat. This fail-open stance is unrelated to (and not
    revisited by) the strict fail-closed auth gate in
    `core_api_client.require_chat_session`, which guards a different,
    primary system (core-api itself); this one guards a genuinely secondary
    system, same as core-api's own rate_limit.py.
    """
    try:
        client = get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        if count <= limit:
            return None
        ttl = await client.ttl(key)
        return ttl if ttl and ttl > 0 else window_seconds
    except RedisError:
        logger.warning("chat rate limit check failed, failing open for key=%s", key, exc_info=True)
        return None


def _raise_429(retry_after: int) -> None:
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too many chat requests, try again later",
        headers={"Retry-After": str(retry_after)},
    )


async def check_chat_rate_limit(user_id: str) -> None:
    """Call once per POST /chat request — both the fresh-message and resume
    paths — right after `require_chat_session` resolves a real `user_id`,
    before any graph/Ollama work. One fixed-window counter per `user_id`,
    incremented atomically on *every* call regardless of outcome (mirrors
    core-api's `check_and_record_signup_attempt`, not its login
    split — the concern here is request *volume* against the single-GPU
    Ollama pipeline, not credential guessing, so there's no "only count
    failures" distinction to make). Keyed by `user_id`, not IP: every caller
    is authenticated by this point, so `user_id` is the meaningful axis — a
    household sharing one IP must not throttle every member off of one
    member's usage.

    This is a real but partial mitigation for GPU contention (scaling-debt
    #14 tracks real concurrency/backpressure limiting separately) — it stops
    a scripted flood from one identity, it does not serialize concurrent
    legitimate users onto the single GPU.
    """
    key = f"ratelimit:chat:user:{user_id}"
    retry_after = await _retry_after_if_limited(
        key, settings.chat_rate_limit_requests, settings.chat_rate_limit_window_seconds
    )
    if retry_after is not None:
        _raise_429(retry_after)
