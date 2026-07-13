"""Lazy-loaded singletons for the two local ML models.

Both bot and worker import this module; whichever process actually calls
get_embedder()/get_cross_encoder() first pays the (near-instant, since
weights are baked into the image) load cost, once per process.
"""
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.config import settings

_embedder: SentenceTransformer | None = None
_cross_encoder: CrossEncoder | None = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(settings.embedding_model_name)
    return _embedder


def get_cross_encoder() -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        _cross_encoder = CrossEncoder(settings.cross_encoder_model_name)
    return _cross_encoder
