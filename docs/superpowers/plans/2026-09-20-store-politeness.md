# Store Politeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce each store's configured `requests_per_second` and add bounded retry/backoff (honouring `Retry-After`) around the HTTP client every adapter already uses, so stores stop challenging us with HTTP 429.

**Architecture:** A new `app/politeness.py` provides a thread-safe `RateLimiter`, a `RetryPolicy`, and a `RetryingHttpClient` that wraps the existing `HttpxClient` behind the `HttpClient` protocol. `HttpError` gains `status_code`/`retry_after`/`challenged` metadata. `build_adapters` wraps the base client once per store, so adapters, matching, and the orchestrator are unchanged.

**Tech Stack:** Python 3.11+, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-09-20-store-politeness-design.md`

## Global Constraints

- Python >= 3.11; run tests with `uv run pytest`.
- **No real network and no real sleeps in tests** — inject `clock`, `sleep`, and `rand`.
- Keep the identifying `User-Agent`; no proxy rotation or anti-bot evasion.
- Adapters, matching, and the orchestrator are **not** modified.
- `HttpError`'s new fields are backward compatible (`status_code=None`, `retry_after=None`, `challenged=False`).
- Retryable failures are exactly `status_code is None`, `429`, and `503`. All other `>= 400` statuses raise immediately (unchanged behaviour).
- Defaults: `attempts=3`, `base_delay=0.5`, `max_delay=8.0`; full jitter; `Retry-After` overrides backoff but is capped at `max_delay`.
- On exhaustion, the raised `HttpError` preserves `status_code`, `retry_after`, and `challenged`, and its message names rate-limiting (and the Cloudflare challenge when detected).
- One commit per task; follow existing repo conventions.

---

### Task 1: `HttpError` metadata

**Files:**
- Modify: `app/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `HttpError(message, status_code=None, retry_after=None, challenged=False)` with attributes `status_code: int | None`, `retry_after: float | None`, `challenged: bool`; `HttpxClient` populates them from the response.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_http.py`

```python
from app.http import HttpError, HttpxClient


def _error_client(handler):
    return HttpxClient("mtgcardtracker/0.1", transport=httpx.MockTransport(handler))


def test_error_carries_status_and_retry_after_seconds():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "2"}, json={})

    with pytest.raises(HttpError) as excinfo:
        _error_client(handler).get_json("https://x.test/a")
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 2.0
    assert excinfo.value.challenged is False


def test_error_flags_cloudflare_challenge():
    def handler(request):
        return httpx.Response(429, headers={"cf-mitigated": "challenge"})

    with pytest.raises(HttpError) as excinfo:
        _error_client(handler).get_text("https://x.test/a")
    assert excinfo.value.challenged is True


def test_retry_after_parses_http_date():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=30)

    def handler(request):
        return httpx.Response(503, headers={"Retry-After": format_datetime(when)})

    with pytest.raises(HttpError) as excinfo:
        _error_client(handler).get_json("https://x.test/a")
    assert excinfo.value.retry_after is not None
    assert 25.0 <= excinfo.value.retry_after <= 30.0


def test_missing_retry_after_is_none():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(HttpError) as excinfo:
        _error_client(handler).get_json("https://x.test/a")
    assert excinfo.value.retry_after is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_http.py -v`
Expected: FAIL with `TypeError: HttpError.__init__() got an unexpected keyword argument 'status_code'` (or `AttributeError`).

- [ ] **Step 3: Implement** — `app/http.py`

Replace the `HttpError` class and the error paths in `get_json`/`get_text`, and add imports:

```python
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
```

```python
class HttpError(Exception):
    def __init__(self, message, status_code=None, retry_after=None, challenged=False):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.challenged = challenged


def _retry_after(response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _raise_for_status(url: str, response) -> None:
    challenged = response.headers.get("cf-mitigated", "").lower() == "challenge"
    raise HttpError(
        f"{url} returned {response.status_code}",
        status_code=response.status_code,
        retry_after=_retry_after(response),
        challenged=challenged,
    )
```

In both `get_json` and `get_text`, replace:

```python
        if response.status_code >= 400:
            raise HttpError(f"{url} returned {response.status_code}")
```

with:

```python
        if response.status_code >= 400:
            _raise_for_status(url, response)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_http.py -v`
Expected: PASS (existing tests plus the four new ones)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/http.py tests/test_http.py
git commit -m "feat: carry status, retry-after and challenge on HttpError"
```

---

### Task 2: `RateLimiter` and `RetryPolicy`

**Files:**
- Create: `app/politeness.py`
- Test: `tests/test_politeness.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RETRYABLE_STATUS = frozenset({429, 503})`
  - `RateLimiter(rate_per_second, clock=time.monotonic, sleep=time.sleep)` with property `interval -> float` and method `wait() -> None`
  - `RetryPolicy(attempts=3, base_delay=0.5, max_delay=8.0)` (frozen dataclass) with `delay_for(attempt: int, retry_after: float | None, rand=random.random) -> float`

- [ ] **Step 1: Write the failing tests** — `tests/test_politeness.py`

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_politeness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.politeness'`

- [ ] **Step 3: Implement** — `app/politeness.py`

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_politeness.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/politeness.py tests/test_politeness.py
git commit -m "feat: per-store rate limiter and retry policy"
```

---

### Task 3: `RetryingHttpClient` and `make_store_client`

**Files:**
- Modify: `app/politeness.py`
- Test: `tests/test_politeness.py`

**Interfaces:**
- Consumes: `HttpError` (Task 1); `RateLimiter`, `RetryPolicy`, `RETRYABLE_STATUS` (Task 2).
- Produces:
  - `RetryingHttpClient(inner, limiter, policy=RetryPolicy(), sleep=time.sleep, rand=random.random)` implementing `get_json(url, params=None)` / `get_text(url, params=None)`, with property `rate_per_second -> float`.
  - `make_store_client(base_http, config, *, policy=None, sleep=time.sleep, clock=time.monotonic, rand=random.random) -> RetryingHttpClient`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_politeness.py`

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_politeness.py -v`
Expected: FAIL with `ImportError: cannot import name 'RetryingHttpClient'`.

- [ ] **Step 3: Implement** — append to `app/politeness.py`

Add the import at the top:

```python
from app.http import HttpError
```

Add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_politeness.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/politeness.py tests/test_politeness.py
git commit -m "feat: retrying per-store http client"
```

---

### Task 4: Wire the wrapper into `build_adapters`

**Files:**
- Modify: `app/adapters/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `make_store_client` (Task 3).
- Produces: `build_adapters(configs, http, browser=None)` now passes each adapter a per-store `RetryingHttpClient`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_registry.py`

```python
from app.politeness import RetryingHttpClient, make_store_client


def _config(**over):
    values = dict(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )
    values.update(over)
    return StoreConfig(**values)


def test_build_adapters_wraps_client_per_store():
    adapters = build_adapters([_config(requests_per_second=0.5)], FakeHttp())
    client = adapters[0]._http
    assert isinstance(client, RetryingHttpClient)
    assert client.rate_per_second == 0.5


def test_make_store_client_reflects_configured_rate():
    client = make_store_client(FakeHttp(), _config(requests_per_second=0.25), sleep=lambda _s: None)
    assert client.rate_per_second == 0.25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL — `adapters[0]._http` is `FakeHttp`, not `RetryingHttpClient`.

- [ ] **Step 3: Implement** — `app/adapters/registry.py`

Add the import:

```python
from app.politeness import make_store_client
```

Change `build_adapters`:

```python
def build_adapters(configs, http, browser=None) -> list[Adapter]:
    adapters = []
    for cfg in configs:
        if not cfg.enabled:
            continue
        cls = _import_class(cfg.adapter_path)
        client = make_store_client(http, cfg)
        if cfg.needs_browser:
            adapters.append(cls(cfg, client, browser))
        else:
            adapters.append(cls(cfg, client))
    return adapters
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions. (Existing adapter contract tests construct adapters directly or call `build_adapters` without issuing requests, so no real rate-limit delays are introduced.)

- [ ] **Step 6: Commit**

```bash
git add app/adapters/registry.py tests/test_registry.py
git commit -m "feat: rate-limit and retry every store adapter via the registry"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: updated docs.

- [ ] **Step 1: Replace the politeness limitation** — `README.md`

Find the bullet in **Known limitations / follow-ups** that begins:

```markdown
- **Politeness config is not wired.** `requests_per_second` in
  `config/stores.yaml` is currently unread, per-store `concurrency` is not
  plumbed to real adapters (they always get the default), and there is no rate
  limiter or retry/backoff. Fine for one store with a 6h cache; needed before
  adding many stores.
```

Replace it with:

```markdown
- **Politeness.** Each store's `requests_per_second` is enforced by a
  thread-safe rate limiter, and transient failures (HTTP `429`, `503`, and
  network/timeout errors) are retried up to three times with exponential
  backoff and jitter, honouring `Retry-After`. A Cloudflare challenge is named
  in the resulting store error. Per-store `concurrency` is still unused, and
  there is no aggregate cross-store request cap.
```

- [ ] **Step 2: Verify**

Run: `grep -n "Politeness" README.md`
Expected: one match containing the new wording.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document store rate limiting and retry/backoff"
```

---

## Self-Review

**Spec coverage**

- Enforce per-store `requests_per_second` on every request → Tasks 2, 3, 4 (`RateLimiter` + wrapper injected by `build_adapters`).
- Retry `429`/`503`/network with exponential backoff + jitter → Tasks 2, 3.
- Honour `Retry-After` (seconds and HTTP-date), capped → Tasks 1, 2, 3.
- Clear error naming rate-limiting and Cloudflare challenge → Tasks 1 (`challenged`), 3 (`_final_error`).
- Keep the change behind the `HttpClient` seam; adapters unchanged → Task 4 (registry only).
- No real network/sleeps in tests → Task 2/3 inject `clock`/`sleep`/`rand`.
- README updated → Task 5.

**Placeholder scan:** every step contains complete code; no TBD/TODO.

**Type consistency:**

- `HttpError(..., status_code, retry_after, challenged)` defined in Task 1 and constructed the same way in Task 3's `_final_error`.
- `RateLimiter.interval` and `RetryingHttpClient.rate_per_second` are used by Task 4's tests.
- `RetryPolicy.delay_for(attempt, retry_after, rand)` signature matches its call in `_call`.
- `make_store_client(base_http, config, *, policy, sleep, clock, rand)` is called by `build_adapters` with only `(http, cfg)` and by tests with keyword overrides.
- `RETRYABLE_STATUS` is the single source of retryable codes, used by `_retryable`.
