from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024  # caps hashing work per request

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
_DUMMY_HASH = _hasher.hash("veyl-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape verify: with no hash (unknown user) we still pay the hashing cost
    so response timing does not reveal which accounts exist."""
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None
