"""Sets dummy required env vars before any app.* module is imported.

app.config reads several env vars directly via os.environ[...] (not .get)
at import time -- Settings() is constructed at module scope in
app/config.py, so importing app.config (or anything that transitively
imports it, which is nearly everything) fails immediately without these.
Mirrors what CI's import-check step already sets.
"""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_USER_IDS", "1")
os.environ.setdefault("WEBHOOK_SECRET_TOKEN", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
