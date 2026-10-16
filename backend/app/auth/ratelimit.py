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

    def _recent(self, key: str) -> deque[float]:
        """Failures for `key` inside the window. Keys with none are dropped, so probing
        many distinct accounts cannot grow memory without bound."""
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

    @classmethod
    def create(cls, *, max_failures: int, window_seconds: float) -> "LoginLimiters":
        return cls(
            account=LoginRateLimiter(max_failures=max_failures, window_seconds=window_seconds),
            ip=LoginRateLimiter(
                max_failures=max_failures * IP_LIMIT_MULTIPLIER, window_seconds=window_seconds
            ),
        )
