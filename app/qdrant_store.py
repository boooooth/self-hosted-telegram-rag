"""Qdrant access: one point per chunk, deterministic UUID point IDs.

Qdrant only accepts unsigned ints or UUIDs as point IDs, so the readable
"{document_id}:{chunk_index}" identifier is derived deterministically into a
UUID5 for the point ID, and kept as-is in the payload (`chunk_id`) so search
results can join straight back to Postgres.
"""
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from app.config import settings
from app.models import get_embedder

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url)
    return _client


def chunk_id_str(document_id: int, chunk_index: int) -> str:
    return f"{document_id}:{chunk_index}"


def point_id(document_id: int, chunk_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, chunk_id_str(document_id, chunk_index)))


def ensure_collection() -> None:
    """Creates the collection sized for whatever EMBEDDING_MODEL_NAME is
    currently configured. If the collection already exists (e.g. from a
    previous run with a different model), verifies its vector size still
    matches — swapping embedding models otherwise fails silently at upsert
    time with an opaque Qdrant dimension-mismatch error."""
    client = get_client()
    dim = get_embedder().get_sentence_embedding_dimension()

    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        return

    existing_dim = client.get_collection(settings.qdrant_collection).config.params.vectors.size
    if existing_dim != dim:
        raise RuntimeError(
            f"Qdrant collection '{settings.qdrant_collection}' holds {existing_dim}-dim "
            f"vectors, but embedding model '{settings.embedding_model_name}' produces "
            f"{dim}-dim vectors. Either revert EMBEDDING_MODEL_NAME or delete the "
            "collection so it can be recreated at the new size."
        )


def upsert_chunks(document_id: int, embeddings: list[list[float]]) -> None:
    points = [
        PointStruct(
            id=point_id(document_id, idx),
            vector=vector,
            payload={
                "chunk_id": chunk_id_str(document_id, idx),
                "document_id": document_id,
                "chunk_index": idx,
            },
        )
        for idx, vector in enumerate(embeddings)
    ]
    get_client().upsert(collection_name=settings.qdrant_collection, points=points)
    _delete_chunks_from_index(document_id, from_index=len(embeddings))


def _delete_chunks_from_index(document_id: int, from_index: int) -> None:
    """Removes points left over from a previous ingestion attempt that
    produced more chunks than this one (e.g. CHUNK_SIZE changed between
    runs) -- otherwise those higher-indexed points stay orphaned and
    searchable forever, mirroring the same cleanup in db.insert_chunks."""
    get_client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                FieldCondition(key="chunk_index", range=Range(gte=from_index)),
            ]
        ),
    )


def delete_document(document_id: int) -> None:
    """Removes every point for a document. Used to clean up a partial write
    left behind when indexing fails permanently (see app.ingestion.notify_final_failure)
    so no orphaned, content-less vectors remain searchable."""
    get_client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        ),
    )


def search(query_vector: list[float], limit: int) -> list[dict]:
    hits = get_client().query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        limit=limit,
    ).points
    return [
        {
            "chunk_id": hit.payload["chunk_id"],
            "document_id": hit.payload["document_id"],
            "score": hit.score,
        }
        for hit in hits
    ]
