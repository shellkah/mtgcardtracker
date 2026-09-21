from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

from app.http import HttpError

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


class RetryingHttpClient:
    """HttpClient wrapper: per-store rate limiting plus bounded retry/backoff."""

    def __init__(self, inner, limiter, policy=RetryPolicy(), sleep=time.sleep, rand=random.random):
        self._inner = inner
        self._limiter = limiter
        self._policy = policy
        self._sleep = sleep
        self._rand = rand

    @property
    def rate_per_second(self) -> float:
        interval = self._limiter.interval
        return 1.0 / interval if interval > 0 else 0.0

    def get_json(self, url, params=None):
        return self._call(self._inner.get_json, url, params)

    def get_text(self, url, params=None):
        return self._call(self._inner.get_text, url, params)

    @staticmethod
    def _retryable(exc: HttpError) -> bool:
        return exc.status_code is None or exc.status_code in RETRYABLE_STATUS

    def _call(self, fn, url, params):
        attempts = max(1, self._policy.attempts)
        for attempt in range(1, attempts + 1):
            self._limiter.wait()
            try:
                return fn(url, params)
            except HttpError as exc:
                if not self._retryable(exc):
                    raise  # permanent failure: unchanged behaviour
                if attempt == attempts:
                    raise self._exhausted_error(exc, attempt) from exc
                self._sleep(self._policy.delay_for(attempt, exc.retry_after, self._rand))

    def _exhausted_error(self, exc: HttpError, attempts: int) -> HttpError:
        if exc.status_code is None:
            return exc  # network/timeout: re-raise the original unchanged
        why = "Cloudflare challenge" if exc.challenged else "rate-limited"
        return HttpError(
            f"{exc} ({why}) after {attempts} attempt(s)",
            status_code=exc.status_code,
            retry_after=exc.retry_after,
            challenged=exc.challenged,
        )


def make_store_client(base_http, config, *, policy=None, sleep=time.sleep,
                      clock=time.monotonic, rand=random.random) -> RetryingHttpClient:
    limiter = RateLimiter(config.requests_per_second, clock=clock, sleep=sleep)
    return RetryingHttpClient(base_http, limiter, policy or RetryPolicy(), sleep=sleep, rand=rand)
