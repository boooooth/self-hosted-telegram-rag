"""Per-user query cooldown, backed by Redis. Cost/abuse guard since reads
are open to any Telegram user and every query calls the Claude API."""
import redis

from app.config import settings

_redis: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(settings.redis_url)
    return _redis


def allow(user_id: int) -> bool:
    """Returns True if the user may proceed, False if still in cooldown."""
    key = f"cooldown:{user_id}"
    return bool(_client().set(key, "1", nx=True, ex=settings.cooldown_seconds))
