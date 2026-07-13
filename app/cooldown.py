"""Per-user query cooldown, backed by Redis. Cost/abuse guard since reads
are open to any Telegram user and every query calls the configured LLM."""
from app.config import settings
from app.redis_client import get_redis_client


def allow(user_id: int) -> bool:
    """Returns True if the user may proceed, False if still in cooldown."""
    key = f"cooldown:{user_id}"
    return bool(get_redis_client().set(key, "1", nx=True, ex=settings.cooldown_seconds))
