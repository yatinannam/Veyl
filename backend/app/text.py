"""Text the database can store. Postgres text cannot hold NUL characters, so request
fields reject them up front (a 422) instead of failing deep inside a transaction."""

import re
from typing import Annotated

from pydantic import AfterValidator

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


SafeText = Annotated[str, AfterValidator(reject_nul)]


def clean_text(value: str) -> str:
    """For extracted document text, which is data rather than input to refuse: drop NUL
    characters and replace lone surrogates (which cannot be encoded) with U+FFFD."""
    return _LONE_SURROGATE.sub("�", value.replace("\x00", ""))
