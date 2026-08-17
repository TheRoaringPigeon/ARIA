import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.rate_limit_redis_url,
            decode_responses=True,
            # Short timeouts matter here: this sits in front of every
            # login/signup request, so a down Redis must fail fast, not hang
            # on the OS TCP connect timeout — same rationale
            # app/celery_client.py applies to its broker connection.
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client
