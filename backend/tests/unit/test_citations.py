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


def test_resolve_citations_accepts_the_marker_styles_models_actually_write() -> None:
    # Seen live from openai/gpt-oss-120b: CJK lenticular brackets, sometimes with a
    # line reference after a dagger; also fullwidth brackets and grouped labels.
    sources = _sources(3)
    answer = (
        "Revenue was 18.4 million USD【S1】. "
        "Cash was 42.7 million【S2†L3-L5】. "
        "Margins held ［S1］, see [S3, S2]; ignore 【S9】."  # noqa: RUF001
    )
    resolved = resolve_citations(answer, sources)
    assert [s.chunk for s in resolved.cited] == [s.chunk for s in sources]
    assert resolved.text == (
        "Revenue was 18.4 million USD[S1]. Cash was 42.7 million[S2]. "
        "Margins held [S1], see [S3][S2]; ignore ."
    )


def test_resolve_citations_without_markers_cites_nothing() -> None:
    resolved = resolve_citations("I could not find that.", _sources(2))
    assert resolved.cited == []
    assert resolved.text == "I could not find that."
