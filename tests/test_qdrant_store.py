"""Tests for app.qdrant_store's pure ID-generation helpers and the
stale-point cleanup added alongside upsert_chunks. The Qdrant client itself
is mocked -- no real Qdrant instance needed.
"""
from unittest.mock import MagicMock

import app.qdrant_store as qdrant_store
from app.qdrant_store import chunk_id_str, point_id


def test_chunk_id_str_format():
    assert chunk_id_str(document_id=42, chunk_index=3) == "42:3"


def test_point_id_is_deterministic():
    assert point_id(42, 3) == point_id(42, 3)


def test_point_id_differs_for_different_chunk_indexes():
    assert point_id(42, 0) != point_id(42, 1)


def test_point_id_differs_for_different_documents():
    assert point_id(1, 0) != point_id(2, 0)


def test_point_id_is_a_valid_uuid_string():
    import uuid

    parsed = uuid.UUID(point_id(1, 0))
    assert str(parsed) == point_id(1, 0)


def test_upsert_chunks_writes_chunk_index_in_payload(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(qdrant_store, "get_client", lambda: mock_client)

    qdrant_store.upsert_chunks(document_id=7, embeddings=[[0.1], [0.2], [0.3]])

    assert mock_client.upsert.called
    points = mock_client.upsert.call_args.kwargs["points"]
    assert [p.payload["chunk_index"] for p in points] == [0, 1, 2]
    assert all(p.payload["document_id"] == 7 for p in points)


def test_upsert_chunks_deletes_stale_points_beyond_new_chunk_count(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(qdrant_store, "get_client", lambda: mock_client)

    qdrant_store.upsert_chunks(document_id=7, embeddings=[[0.1], [0.2]])

    assert mock_client.delete.called
    delete_kwargs = mock_client.delete.call_args.kwargs
    assert delete_kwargs["collection_name"] == qdrant_store.settings.qdrant_collection

    conditions = {c.key: c for c in delete_kwargs["points_selector"].must}
    assert conditions["document_id"].match.value == 7
    # 2 embeddings were written (indexes 0, 1) -- anything at index >= 2 is stale
    assert conditions["chunk_index"].range.gte == 2
