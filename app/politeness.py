from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

RETRYABLE_STATUS = frozenset({429, 503})


class RateLimiter:
    """Thread-safe minimum interval between calls to one store.

    ``rate_per_second <= 0`` disables limiting. The clock and sleep are
    injectable so tests can run without real delays.
    """

    def __init__(self, rate_per_second: float, clock=time.monotonic, sleep=time.sleep):
        if rate_per_second and rate_per_second > 0:
            self._interval = 1.0 / rate_per_second
        else:
            self._interval = 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = self._clock()
            delay = self._next_allowed - now
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed) + self._interval


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay_for(self, attempt: int, retry_after: float | None, rand=random.random) -> float:
        """Seconds to wait after a failed attempt (attempt is 1-based).

        ``Retry-After`` wins but is capped at ``max_delay``; otherwise full
        jitter over an exponentially growing ceiling.
        """
        if retry_after is not None:
            return min(self.max_delay, max(0.0, retry_after))
        ceiling = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return rand() * ceiling
