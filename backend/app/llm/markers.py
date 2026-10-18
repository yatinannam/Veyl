"""Source markers in model text, shared by the citation check and the prompt builder.

Models write them in several shapes: [S1], [S1, S3], fullwidth brackets, and (gpt-oss)
CJK lenticular brackets with an optional short line reference after a dagger. The
reference may not cross a line or exceed 40 characters, so a stray dagger can never
swallow answer text.
"""

import re

_OPEN = "[［【"  # noqa: RUF001 (deliberate look-alikes)
_CLOSE = "]］】"  # noqa: RUF001 (deliberate look-alikes)
MARKER = re.compile(
    rf"[{re.escape(_OPEN)}]\s*([Ss]\d{{1,3}}(?:\s*,\s*[Ss]\d{{1,3}})*)\s*"
    rf"(?:†[^{re.escape(_CLOSE)}\n]{{0,40}})?[{re.escape(_CLOSE)}]"
)
_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r" +([.,;:!?])")


def collapse_spaces(text: str) -> str:
    return _SPACES.sub(" ", text)


def strip_markers(text: str) -> str:
    """Remove every source marker. Used for replayed history: an old [S1] referred to the
    sources of that earlier turn and must not be read as a label for the current ones."""
    without = collapse_spaces(MARKER.sub("", text))
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", without).strip()
