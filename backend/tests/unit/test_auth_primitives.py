import hashlib

from app.auth.passwords import hash_password, verify_password
from app.auth.ratelimit import LoginLimiters, LoginRateLimiter
from app.auth.tokens import hash_token, new_csrf_token, new_session_token


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong password!!")


def test_verify_without_a_hash_is_false() -> None:
    assert verify_password(None, "anything at all") is False


def test_verify_with_a_malformed_hash_is_false() -> None:
    assert verify_password("not-a-real-hash", "anything at all") is False


def test_session_tokens_are_unique_and_long() -> None:
    tokens = {new_session_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 for t in tokens)


def test_csrf_tokens_are_unique() -> None:
    assert new_csrf_token() != new_csrf_token()


def test_token_hash_is_a_sha256_digest() -> None:
    assert hash_token("abc") == hashlib.sha256(b"abc").digest()
    assert len(hash_token("abc")) == 32


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_blocks_after_max_failures_in_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60, clock=clock)
    for _ in range(2):
        limiter.record_failure("k")
    assert not limiter.is_blocked("k")
    limiter.record_failure("k")
    assert limiter.is_blocked("k")
    assert not limiter.is_blocked("other")


def test_limiter_forgets_failures_after_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60, clock=clock)
    limiter.record_failure("k")
    assert limiter.is_blocked("k")
    clock.now += 61
    assert not limiter.is_blocked("k")


def test_limiter_reset_clears_key() -> None:
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60)
    limiter.record_failure("k")
    limiter.reset("k")
    assert not limiter.is_blocked("k")


def test_ip_limit_is_looser_than_account_limit() -> None:
    limiters = LoginLimiters.create(max_failures=2, window_seconds=60)
    for _ in range(2):
        limiters.account.record_failure("a")
        limiters.ip.record_failure("i")
    assert limiters.account.is_blocked("a")
    assert not limiters.ip.is_blocked("i")


def test_limiter_does_not_retain_keys_without_recent_failures() -> None:
    # Probing many distinct accounts must not grow memory without bound.
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60, clock=clock)
    for i in range(1000):
        limiter.is_blocked(f"probe-{i}")
    limiter.record_failure("k")
    clock.now += 61
    limiter.is_blocked("k")
    assert limiter.tracked_keys() == 0
