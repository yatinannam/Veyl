"""Why a database connection failed, safe to log: the driver's own message with every known
secret, URL credential, and password setting removed."""

import re
from collections.abc import Iterable

_URL_CREDENTIALS = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")
_PASSWORD_SETTING = re.compile(r"(password\s*=\s*)\S+", re.IGNORECASE)
MAX_LENGTH = 400


def describe_db_error(error: BaseException, secrets: Iterable[str | None] = ()) -> str:
    cause = getattr(error, "orig", None) or error
    message = " ".join(str(cause).split())
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    message = _URL_CREDENTIALS.sub(r"\1***@", message)
    message = _PASSWORD_SETTING.sub(r"\1***", message)
    return f"{type(cause).__name__}: {message}"[:MAX_LENGTH]
