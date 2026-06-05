from sentence_transformers import SentenceTransformer

from .config import get_settings

_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        s = get_settings()
        _model = SentenceTransformer(s.embedding_model)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vecs = model.encode(texts, normalize_embeddings=True)
    return vecs.tolist()
