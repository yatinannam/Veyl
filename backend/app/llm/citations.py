"""Server-side citation checking: only labels the server supplied survive, and the
citation list is built from the authorized rows, never from model text."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.llm.prompt import PromptSource

# Models write source markers in several shapes: [S1], [S1, S3], fullwidth brackets, and
# (gpt-oss) CJK lenticular brackets with an optional line reference after a dagger,
# such as 【S1†L3-L5】. All are read; the stored text always uses [S#].
_OPEN = "[［【"  # noqa: RUF001 (deliberate look-alikes)
_CLOSE = "]］】"  # noqa: RUF001 (deliberate look-alikes)
_MARKER = re.compile(
    rf"[{re.escape(_OPEN)}]\s*([Ss]\d{{1,3}}(?:\s*,\s*[Ss]\d{{1,3}})*)\s*"
    rf"(?:†[^{re.escape(_CLOSE)}]*)?[{re.escape(_CLOSE)}]"
)
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

    def relabel(label: str) -> str:
        source = by_label.get(label.strip().upper())
        if source is None:
            return ""
        if source.label not in renumbered:
            renumbered[source.label] = PromptSource(f"S{len(renumbered) + 1}", source.chunk)
        return f"[{renumbered[source.label].label}]"

    def replace(match: re.Match[str]) -> str:
        return "".join(relabel(label) for label in match.group(1).split(","))

    text = _SPACES.sub(" ", _MARKER.sub(replace, answer)).strip()
    return ResolvedAnswer(text=text, cited=list(renumbered.values()))


def make_snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 3].rstrip() + "..."
