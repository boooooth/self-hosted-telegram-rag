"""RQ queue factory bound to Redis, used by the bot to enqueue ingestion jobs
and by the worker to consume them."""
import redis
from rq import Queue

from app.config import settings

_queue: Queue | None = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        conn = redis.Redis.from_url(settings.redis_url)
        _queue = Queue("default", connection=conn)
    return _queue
