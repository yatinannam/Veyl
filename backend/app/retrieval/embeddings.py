import threading
from collections.abc import Callable, Sequence
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

    def __init__(
        self, model_name: str, cache_dir: str | None = None, batch_size: int = 256
    ) -> None:
        from fastembed import TextEmbedding  # heavy import, deferred

        self.model_name = model_name
        # Smaller batches lower peak memory (activations grow with the batch); 256 is the
        # library default.
        self._batch_size = batch_size
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.passage_embed(list(texts), batch_size=self._batch_size)
        return [_checked(v) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return _checked(next(iter(self._model.query_embed(text))))


class LazyEmbedder:
    """Builds the real embedder on first use, so the API starts fast and tests that never
    query never load the model."""

    def __init__(self, factory: Callable[[], Embedder], model_name: str) -> None:
        self.model_name = model_name
        self._factory = factory
        self._inner: Embedder | None = None
        self._lock = threading.Lock()

    def _get(self) -> Embedder:
        with self._lock:
            if self._inner is None:
                self._inner = self._factory()
            return self._inner

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._get().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._get().embed_query(text)
