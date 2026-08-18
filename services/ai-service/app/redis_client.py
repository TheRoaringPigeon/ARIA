import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.chat_rate_limit_redis_url,
            decode_responses=True,
            # Short timeouts: this sits in front of every /chat request, so
            # a down Redis must fail fast, not hang on the OS TCP connect
            # timeout — same rationale core-api's redis_client.py applies to
            # its own rate-limit Redis connection.
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client
