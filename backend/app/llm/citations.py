"""Server-side citation checking: only labels the server supplied survive, and the
citation list is built from the authorized rows, never from model text."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.llm.prompt import PromptSource

_MARKER = re.compile(r"\[\s*([Ss]\d{1,3})\s*\]")
_SPACES = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    text: str
    cited: list[PromptSource] = field(default_factory=list)


def resolve_citations(answer: str, sources: Sequence[PromptSource]) -> ResolvedAnswer:
    by_label = {s.label: s for s in sources}
    cited: list[PromptSource] = []

    def replace(match: re.Match[str]) -> str:
        label = match.group(1).upper()
        source = by_label.get(label)
        if source is None:
            return ""
        if source not in cited:
            cited.append(source)
        return f"[{label}]"

    text = _SPACES.sub(" ", _MARKER.sub(replace, answer)).strip()
    return ResolvedAnswer(text=text, cited=cited)
