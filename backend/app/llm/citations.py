"""Server-side citation checking: only labels the server supplied survive, and the
citation list is built from the authorized rows, never from model text."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.llm.prompt import PromptSource

_MARKER = re.compile(r"\[\s*([Ss]\d{1,3})\s*\]")
_SPACES = re.compile(r"[ \t]{2,}")


SNIPPET_CHARS = 240


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    """`cited` is renumbered S1..Sm in order of first appearance, and the text uses the
    new labels, so a stored answer maps label n to the n-th stored cited chunk."""

    text: str
    cited: list[PromptSource] = field(default_factory=list)


def resolve_citations(answer: str, sources: Sequence[PromptSource]) -> ResolvedAnswer:
    by_label = {s.label: s for s in sources}
    renumbered: dict[str, PromptSource] = {}

    def replace(match: re.Match[str]) -> str:
        source = by_label.get(match.group(1).upper())
        if source is None:
            return ""
        if source.label not in renumbered:
            renumbered[source.label] = PromptSource(f"S{len(renumbered) + 1}", source.chunk)
        return f"[{renumbered[source.label].label}]"

    text = _SPACES.sub(" ", _MARKER.sub(replace, answer)).strip()
    return ResolvedAnswer(text=text, cited=list(renumbered.values()))


def make_snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 3].rstrip() + "..."
