import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

IP_LIMIT_MULTIPLIER = 4


class LoginRateLimiter:
    """Sliding-window failure counter, in-process (single API replica; spec section 11)."""

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    def _sweep(self) -> None:
        """Drop every key whose newest failure has left the window. Runs at most once per
        window, so memory is bounded by the keys that failed in the last two windows."""
        now = self._clock()
        if now - self._last_sweep < self._window:
            return
        cutoff = now - self._window
        for key in [k for k, entries in self._failures.items() if entries[-1] <= cutoff]:
            del self._failures[key]
        self._last_sweep = now

    def _recent(self, key: str) -> deque[float]:
        """Failures for `key` inside the window. A key with none left is dropped."""
        cutoff = self._clock() - self._window
        entries = self._failures.get(key, deque())
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._sweep()
            entries = self._recent(key)
            entries.append(self._clock())
            self._failures[key] = entries

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._failures)


@dataclass(frozen=True, slots=True)
class LoginLimiters:
    account: LoginRateLimiter
    ip: LoginRateLimiter
    # Caps concurrent password verifications whatever the client identity, so a login
    # flood cannot exhaust memory or every worker thread.
    hashing: threading.BoundedSemaphore
    hash_wait_seconds: float

    @classmethod
    def create(
        cls,
        *,
        max_failures: int,
        window_seconds: float,
        max_concurrent_hashes: int = 4,
        hash_wait_seconds: float = 2.0,
    ) -> "LoginLimiters":
        return cls(
            account=LoginRateLimiter(max_failures=max_failures, window_seconds=window_seconds),
            ip=LoginRateLimiter(
                max_failures=max_failures * IP_LIMIT_MULTIPLIER, window_seconds=window_seconds
            ),
            hashing=threading.BoundedSemaphore(max_concurrent_hashes),
            hash_wait_seconds=hash_wait_seconds,
        )
