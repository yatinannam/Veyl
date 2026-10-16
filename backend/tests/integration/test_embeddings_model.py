"""Runs the real local embedding model (downloaded once, about 130 MB). Skip with
VEYL_SKIP_MODEL_TESTS=1 when offline."""

import math
import os

import pytest

from app.retrieval.embeddings import FastEmbedEmbedder

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(os.environ.get("VEYL_SKIP_MODEL_TESTS") == "1", reason="offline"),
]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_bge_small_embeds_384_dims_and_ranks_related_text_higher() -> None:
    embedder = FastEmbedEmbedder("BAAI/bge-small-en-v1.5")
    related, unrelated = embedder.embed_documents(
        [
            "Customers can request a refund within 30 days of purchase.",
            "The cafeteria serves lunch from noon until two.",
        ]
    )
    query = embedder.embed_query("What is the refund policy?")
    assert len(query) == len(related) == 384
    assert _cosine(query, related) > _cosine(query, unrelated)
