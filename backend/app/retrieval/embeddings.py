from collections.abc import Sequence
from typing import Any, Protocol

from app.db.schema import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _checked(vector: Any) -> list[float]:
    values = [float(x) for x in vector]
    if len(values) != EMBEDDING_DIM:
        raise EmbeddingError(f"expected {EMBEDDING_DIM} dimensions, got {len(values)}")
    return values


class FastEmbedEmbedder:
    """Local ONNX embeddings (no API key, no network after the model is cached)."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # heavy import, deferred

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_checked(v) for v in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return _checked(next(iter(self._model.query_embed(text))))
