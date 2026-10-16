"""Paragraph-aware splitter: paragraphs, then sentences, then hard cuts. Markdown headings
start a new chunk so a section stays with its heading. Consecutive chunks overlap by up to
`overlap` characters (cut at a word boundary)."""

import re
from itertools import pairwise

TARGET_CHARS = 800
OVERLAP_CHARS = 120

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> list[str]:
    normalised = text.replace("\r\n", "\n")
    return [p.strip() for p in _PARAGRAPH_BREAK.split(normalised) if p.strip()]


def _pieces(paragraph: str, target: int) -> list[str]:
    if len(paragraph) <= target:
        return [paragraph]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= target:
            pieces.append(sentence)
        else:
            pieces.extend(sentence[i : i + target] for i in range(0, len(sentence), target))
    return pieces


def _tail(chunk: str, overlap: int) -> str:
    if len(chunk) <= overlap:
        return chunk
    tail = chunk[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def chunk_text(text: str, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append("\n\n".join(current))
        current, size = [], 0

    for paragraph in _paragraphs(text):
        if paragraph.startswith("#"):
            flush()
        for piece in _pieces(paragraph, target):
            separator = 2 if current else 0
            if current and size + separator + len(piece) > target:
                flush()
                separator = 0
            current.append(piece)
            size += separator + len(piece)
    flush()

    if overlap <= 0 or len(chunks) < 2:
        return chunks
    return [chunks[0]] + [
        f"{_tail(previous, overlap)} {chunk}" for previous, chunk in pairwise(chunks)
    ]
