"""Entrypoint: `python -m app.worker`. Long-running RQ worker process,
separate container from the bot, sharing Postgres/Redis/Qdrant/disk."""
import logging

import redis
from rq import Queue, Worker

from app.config import settings
from app.qdrant_store import ensure_collection

logging.basicConfig(level=logging.INFO)


def main() -> None:
    ensure_collection()
    conn = redis.Redis.from_url(settings.redis_url)
    worker = Worker([Queue("default", connection=conn)], connection=conn)
    # with_scheduler=True: required for delayed job retries (see app.bot's
    # Retry(interval=...)) to actually re-enter the queue when their
    # scheduled time arrives — off by default in RQ, which otherwise leaves
    # scheduled retries parked forever with nothing polling to promote them.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
