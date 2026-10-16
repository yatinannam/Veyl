from uuid import uuid4

from app.authz import ChunkAcl, Classification
from app.llm.citations import resolve_citations
from app.llm.prompt import PromptSource
from app.retrieval.search import RetrievedChunk


def _sources(n: int) -> list[PromptSource]:
    acl = ChunkAcl(uuid4(), frozenset(), frozenset(), Classification.INTERNAL)
    return [
        PromptSource(
            f"S{i + 1}",
            RetrievedChunk(uuid4(), uuid4(), f"Doc {i + 1}", 0, f"text {i + 1}", 0.9, acl),
        )
        for i in range(n)
    ]


def test_resolve_citations_keeps_only_supplied_labels() -> None:
    sources = _sources(2)
    resolved = resolve_citations("Revenue grew [S1][S7] and costs fell [S42].", sources)
    assert [s.label for s in resolved.cited] == ["S1"]
    assert "[S7]" not in resolved.text and "[S42]" not in resolved.text
    assert "[S1]" in resolved.text


def test_resolve_citations_deduplicates_and_renumbers_by_first_appearance() -> None:
    sources = _sources(3)
    resolved = resolve_citations("A [S2]. B [S1]. C [S2][s1].", sources)
    assert [s.label for s in resolved.cited] == ["S1", "S2"]
    assert [s.chunk for s in resolved.cited] == [sources[1].chunk, sources[0].chunk]
    assert resolved.text == "A [S1]. B [S2]. C [S1][S2]."


def test_resolve_citations_without_markers_cites_nothing() -> None:
    resolved = resolve_citations("I could not find that.", _sources(2))
    assert resolved.cited == []
    assert resolved.text == "I could not find that."
