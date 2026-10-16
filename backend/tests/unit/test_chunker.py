from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from app.ingestion.chunker import chunk_text

TARGET, OVERLAP = 200, 40


def test_empty_text_has_no_chunks() -> None:
    assert chunk_text("   \n\n  ") == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("Hello world.") == ["Hello world."]


def test_long_text_is_split_with_bounded_chunk_size() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(20))
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    assert len(chunks) > 1
    assert all(len(c) <= TARGET + OVERLAP + 1 for c in chunks)


def test_consecutive_chunks_overlap() -> None:
    text = "\n\n".join(f"Sentence number {i} talks about topic {i}." for i in range(40))
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    for previous, current in pairwise(chunks):
        assert previous.split()[-1] in current


def test_markdown_heading_starts_a_new_chunk_with_its_section() -> None:
    text = "Intro paragraph.\n\n# Pricing\n\nPlans start at ten dollars."
    chunks = chunk_text(text, target=TARGET, overlap=0)
    assert chunks[0] == "Intro paragraph."
    assert "# Pricing" in chunks[1] and "Plans start at ten dollars." in chunks[1]


def test_oversized_words_are_hard_cut() -> None:
    chunks = chunk_text("x" * 1000, target=TARGET, overlap=0)
    assert all(len(c) <= TARGET for c in chunks)
    assert "".join(chunks) == "x" * 1000


@given(
    st.text(
        alphabet=st.characters(codec="utf-8", categories=["L", "N", "Zs"]) | st.just("\n"),
        max_size=3000,
    )
)
def test_chunks_are_non_empty_bounded_and_cover_every_word(text: str) -> None:
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    assert all(c.strip() for c in chunks)
    assert all(len(c) <= TARGET + OVERLAP + 1 for c in chunks)
    joined = " ".join(chunks)
    for word in text.split():
        if len(word) <= TARGET:
            assert word in joined
