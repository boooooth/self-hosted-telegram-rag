"""Shared Redis client singleton. Used by both app.queue (RQ) and
app.cooldown (per-user rate limiting) so the two don't each maintain a
separate connection pool to the same Redis instance."""
import redis

from app.config import settings

_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url)
    return _client
