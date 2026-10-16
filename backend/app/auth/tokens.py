import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Session tokens are stored only as this digest; a database leak yields no usable tokens."""
    return hashlib.sha256(token.encode("utf-8")).digest()
