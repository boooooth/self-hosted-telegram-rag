"""Tests for app.retrieval's hybrid merge/dedupe and rerank logic.

All DB/Qdrant/model calls are mocked -- no real Postgres, Qdrant, or ML
model load needed. retrieval.py does `from app.models import
get_cross_encoder, get_embedder`, binding those names directly into its own
module namespace, so they're patched as `retrieval.get_embedder` /
`retrieval.get_cross_encoder` (not `app.models.get_embedder`) below.
"""
import app.retrieval as retrieval


class _FakeVector:
    def tolist(self):
        return [0.1, 0.2, 0.3]


class _FakeEmbedder:
    def encode(self, text):
        return _FakeVector()


class _FakeCrossEncoder:
    def __init__(self, scores):
        self._scores = scores

    def predict(self, pairs):
        return self._scores


def test_hybrid_search_prefers_fts_content_for_chunks_found_in_both(monkeypatch):
    monkeypatch.setattr(retrieval, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(
        retrieval.qdrant_store,
        "search",
        lambda query_vector, limit: [{"chunk_id": "1:0", "document_id": 1, "score": 0.9}],
    )
    monkeypatch.setattr(
        retrieval.db,
        "search_chunks_fts",
        lambda query, limit: [
            {"chunk_id": "1:0", "document_id": 1, "content": "fts content", "rank": 0.5}
        ],
    )
    hydrate_calls = []
    monkeypatch.setattr(
        retrieval.db,
        "get_chunks_by_ids",
        lambda ids: hydrate_calls.append(list(ids)) or {},
    )

    results = retrieval.hybrid_search("some question")

    assert len(results) == 1
    assert results[0]["chunk_id"] == "1:0"
    assert results[0]["content"] == "fts content"
    # the dense hit was already covered by the FTS result -- no hydration needed
    assert hydrate_calls == [[]]


def test_hybrid_search_hydrates_dense_only_hits_from_postgres(monkeypatch):
    monkeypatch.setattr(retrieval, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(
        retrieval.qdrant_store,
        "search",
        lambda query_vector, limit: [{"chunk_id": "2:0", "document_id": 2, "score": 0.8}],
    )
    monkeypatch.setattr(retrieval.db, "search_chunks_fts", lambda query, limit: [])
    monkeypatch.setattr(
        retrieval.db,
        "get_chunks_by_ids",
        lambda ids: {"2:0": {"chunk_id": "2:0", "document_id": 2, "content": "dense content"}},
    )

    results = retrieval.hybrid_search("some question")

    assert len(results) == 1
    assert results[0]["content"] == "dense content"


def test_hybrid_search_returns_empty_when_nothing_found(monkeypatch):
    monkeypatch.setattr(retrieval, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(retrieval.qdrant_store, "search", lambda query_vector, limit: [])
    monkeypatch.setattr(retrieval.db, "search_chunks_fts", lambda query, limit: [])
    monkeypatch.setattr(retrieval.db, "get_chunks_by_ids", lambda ids: {})

    assert retrieval.hybrid_search("some question") == []


def test_hybrid_search_skips_dense_hits_with_no_matching_postgres_row(monkeypatch):
    # A chunk_id Qdrant knows about but Postgres no longer has (e.g. deleted
    # between search and hydration) shouldn't crash or appear as a bare
    # content-less result.
    monkeypatch.setattr(retrieval, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(
        retrieval.qdrant_store,
        "search",
        lambda query_vector, limit: [{"chunk_id": "3:0", "document_id": 3, "score": 0.7}],
    )
    monkeypatch.setattr(retrieval.db, "search_chunks_fts", lambda query, limit: [])
    monkeypatch.setattr(retrieval.db, "get_chunks_by_ids", lambda ids: {})

    assert retrieval.hybrid_search("some question") == []


def test_rerank_returns_empty_for_no_candidates():
    assert retrieval.rerank("question", []) == []


def test_rerank_sorts_by_score_descending(monkeypatch):
    monkeypatch.setattr(retrieval, "get_cross_encoder", lambda: _FakeCrossEncoder([0.1, 0.9, 0.5]))
    candidates = [
        {"chunk_id": "a", "content": "low"},
        {"chunk_id": "b", "content": "high"},
        {"chunk_id": "c", "content": "mid"},
    ]

    ranked = retrieval.rerank("question", candidates)

    assert [c["chunk_id"] for c in ranked] == ["b", "c", "a"]


def test_rerank_respects_configured_top_k(monkeypatch):
    from app.config import settings

    scores = [float(i) for i in range(10)]
    candidates = [{"chunk_id": str(i), "content": "x"} for i in range(10)]
    monkeypatch.setattr(retrieval, "get_cross_encoder", lambda: _FakeCrossEncoder(scores))

    ranked = retrieval.rerank("question", candidates)

    assert len(ranked) == min(settings.rerank_top_k, len(candidates))
