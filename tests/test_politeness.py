import pytest

from app.politeness import RETRYABLE_STATUS, RateLimiter, RetryPolicy


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    limiter = RateLimiter(2.0, clock=clock, sleep=sleep)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert slept == [0.5, 0.5]


def test_rate_limiter_disabled_when_nonpositive():
    limiter = RateLimiter(0)
    assert limiter.interval == 0.0
    limiter.wait()  # no-op, must not raise


def test_retryable_statuses():
    assert RETRYABLE_STATUS == frozenset({429, 503})


def test_retry_policy_full_jitter_and_caps():
    policy = RetryPolicy(attempts=3, base_delay=0.5, max_delay=8.0)
    one = lambda: 1.0
    assert policy.delay_for(1, None, rand=one) == 0.5
    assert policy.delay_for(2, None, rand=one) == 1.0
    assert policy.delay_for(3, None, rand=one) == 2.0
    assert policy.delay_for(10, None, rand=one) == 8.0
    assert policy.delay_for(1, None, rand=lambda: 0.0) == 0.0


def test_retry_policy_retry_after_overrides_and_is_capped():
    policy = RetryPolicy(attempts=3, base_delay=0.5, max_delay=8.0)
    assert policy.delay_for(1, 2.5, rand=lambda: 1.0) == 2.5
    assert policy.delay_for(1, 100.0, rand=lambda: 1.0) == 8.0
