"""RQ queue factory bound to Redis, used by the bot to enqueue ingestion jobs
and by the worker to consume them."""
from rq import Queue

from app.redis_client import get_redis_client

_queue: Queue | None = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("default", connection=get_redis_client())
    return _queue
