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


from app.http import HttpError
from app.politeness import RetryingHttpClient, make_store_client
from app.adapters.registry import StoreConfig


class ScriptedHttp:
    """Fake HttpClient yielding a scripted sequence of payloads/errors."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get_json(self, url, params=None):
        return self._next()

    def get_text(self, url, params=None):
        return self._next()


def _wrapped(inner, sleeps, *, attempts=3, rand=lambda: 1.0):
    limiter = RateLimiter(0)
    policy = RetryPolicy(attempts=attempts, base_delay=0.5, max_delay=8.0)
    return RetryingHttpClient(inner, limiter, policy, sleep=sleeps.append, rand=rand)


def test_retries_then_succeeds():
    inner = ScriptedHttp([HttpError("429", status_code=429),
                          HttpError("429", status_code=429), {"ok": True}])
    sleeps: list[float] = []
    assert _wrapped(inner, sleeps).get_json("https://x.test") == {"ok": True}
    assert inner.calls == 3
    assert sleeps == [0.5, 1.0]


def test_exhaustion_raises_with_status_and_message():
    inner = ScriptedHttp([HttpError("429", status_code=429)] * 3)
    sleeps: list[float] = []
    with pytest.raises(HttpError) as excinfo:
        _wrapped(inner, sleeps).get_json("https://x.test")
    assert excinfo.value.status_code == 429
    assert inner.calls == 3
    assert "429" in str(excinfo.value)


def test_challenge_named_in_final_error():
    inner = ScriptedHttp([HttpError("429", status_code=429, challenged=True)] * 3)
    with pytest.raises(HttpError) as excinfo:
        _wrapped(inner, []).get_json("https://x.test")
    assert excinfo.value.challenged is True
    assert "challenge" in str(excinfo.value).lower()


def test_non_retryable_status_raises_immediately():
    inner = ScriptedHttp([HttpError("404", status_code=404), {"ok": True}])
    sleeps: list[float] = []
    with pytest.raises(HttpError):
        _wrapped(inner, sleeps).get_json("https://x.test")
    assert inner.calls == 1
    assert sleeps == []


def test_retry_after_overrides_backoff():
    inner = ScriptedHttp([HttpError("429", status_code=429, retry_after=2.0), {"ok": True}])
    sleeps: list[float] = []
    _wrapped(inner, sleeps).get_json("https://x.test")
    assert sleeps == [2.0]


def test_network_error_is_retried():
    inner = ScriptedHttp([HttpError("boom"), {"ok": True}])
    sleeps: list[float] = []
    assert _wrapped(inner, sleeps).get_json("https://x.test") == {"ok": True}
    assert inner.calls == 2


def test_limiter_waited_before_each_attempt():
    clock = FakeClock()
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    limiter = RateLimiter(1.0, clock=clock, sleep=sleep)
    inner = ScriptedHttp([HttpError("429", status_code=429), {"ok": True}])
    policy = RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0)
    client = RetryingHttpClient(inner, limiter, policy, sleep=lambda _s: None, rand=lambda: 1.0)
    client.get_json("https://x.test")
    assert slept == [1.0]  # limiter spaced the second attempt by one second


def test_make_store_client_uses_config_rate():
    config = StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com", requests_per_second=0.25,
    )
    client = make_store_client(ScriptedHttp([{"ok": True}]), config, sleep=lambda _s: None)
    assert isinstance(client, RetryingHttpClient)
    assert client.rate_per_second == 0.25
