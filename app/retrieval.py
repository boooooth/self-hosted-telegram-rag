"""Hybrid retrieval (dense + lexical, merged/deduped) and cross-encoder
reranking. Both functions are synchronous/CPU-bound; callers in bot.py run
them via loop.run_in_executor so they don't block the webhook event loop."""
from app import db, qdrant_store
from app.config import settings
from app.models import get_cross_encoder, get_embedder


def hybrid_search(query: str) -> list[dict]:
    query_vector = get_embedder().encode(query).tolist()

    dense_hits = qdrant_store.search(query_vector, limit=settings.candidate_pool_size)
    fts_hits = db.search_chunks_fts(query, limit=settings.candidate_pool_size)

    merged: dict[str, dict] = {
        hit["chunk_id"]: {
            "chunk_id": hit["chunk_id"],
            "document_id": hit["document_id"],
            "content": hit["content"],
        }
        for hit in fts_hits
    }

    missing_ids = [hit["chunk_id"] for hit in dense_hits if hit["chunk_id"] not in merged]
    hydrated = db.get_chunks_by_ids(missing_ids)
    for hit in dense_hits:
        row = hydrated.get(hit["chunk_id"])
        if row is not None and hit["chunk_id"] not in merged:
            merged[hit["chunk_id"]] = {
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "content": row["content"],
            }

    return list(merged.values())[: settings.candidate_pool_size]


def rerank(query: str, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    pairs = [(query, c["content"]) for c in candidates]
    scores = get_cross_encoder().predict(pairs)
    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, _ in ranked[: settings.rerank_top_k]]
