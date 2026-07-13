"""Tests for app.config.Settings' chunk_overlap/chunk_size validation,
added after a misconfigured CHUNK_OVERLAP >= CHUNK_SIZE was found to hang
ingestion.chunk_text in an infinite loop."""
import pytest

from app.config import Settings


def _settings(**overrides):
    """Builds a Settings instance with valid required fields, overridable
    by keyword -- avoids depending on conftest's dummy env vars for the
    fields this test suite actually cares about."""
    defaults = dict(
        telegram_bot_token="test-token",
        admin_user_ids=frozenset({1}),
        webhook_secret_token="test-secret",
        database_url="postgresql://test:test@localhost/test",
        redis_url="redis://localhost:6379/0",
        qdrant_url="http://localhost:6333",
        chunk_size=800,
        chunk_overlap=150,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_valid_chunk_overlap_does_not_raise():
    _settings(chunk_size=800, chunk_overlap=150)


def test_chunk_overlap_equal_to_size_raises():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        _settings(chunk_size=800, chunk_overlap=800)


def test_chunk_overlap_greater_than_size_raises():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        _settings(chunk_size=800, chunk_overlap=900)


def test_chunk_overlap_one_less_than_size_is_the_valid_boundary():
    _settings(chunk_size=800, chunk_overlap=799)


def test_zero_overlap_is_valid():
    _settings(chunk_size=800, chunk_overlap=0)
