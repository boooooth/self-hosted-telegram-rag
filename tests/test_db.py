"""Tests for app.db.insert_chunks's stale-row cleanup, using a mocked
cursor -- no real Postgres needed. The rest of db.py is thin, directly
parameterized SQL passthrough not worth mocking statement-by-statement.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import app.db as db


def test_insert_chunks_upserts_then_deletes_rows_beyond_new_count(monkeypatch):
    mock_cursor = MagicMock()

    @contextmanager
    def fake_cursor():
        yield mock_cursor

    monkeypatch.setattr(db, "_cursor", fake_cursor)

    db.insert_chunks(document_id=42, chunk_texts=["a", "b", "c"])

    assert mock_cursor.executemany.called
    upsert_sql, upsert_rows = mock_cursor.executemany.call_args[0]
    assert "INSERT INTO chunks" in upsert_sql
    assert upsert_rows == [(42, 0, "a"), (42, 1, "b"), (42, 2, "c")]

    mock_cursor.execute.assert_called_once()
    delete_sql, delete_params = mock_cursor.execute.call_args[0]
    assert "DELETE FROM chunks" in delete_sql
    # 3 chunks were written (indexes 0, 1, 2) -- anything at index >= 3 is stale
    assert delete_params == (42, 3)


def test_insert_chunks_with_empty_list_deletes_everything_for_the_document(monkeypatch):
    mock_cursor = MagicMock()

    @contextmanager
    def fake_cursor():
        yield mock_cursor

    monkeypatch.setattr(db, "_cursor", fake_cursor)

    db.insert_chunks(document_id=42, chunk_texts=[])

    delete_sql, delete_params = mock_cursor.execute.call_args[0]
    assert delete_params == (42, 0)
