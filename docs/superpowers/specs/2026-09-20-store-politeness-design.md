# Store Politeness: Rate Limiting + Retry/Backoff

**Date:** 2026-09-20
**Status:** Approved design, pending implementation plan
**Parent spec:** `docs/superpowers/specs/2026-09-19-mtg-card-tracker-design.md`

## 1. Summary

Wire the long-documented-but-unimplemented politeness settings and add bounded
retry/backoff. Today `requests_per_second` in `config/stores.yaml` is parsed
into `StoreConfig` but never read; adapters expose no `concurrency`, so the
orchestrator always uses its fallback; and there is no retry or backoff. During
development the stores' Cloudflare started serving a bot **challenge**
(`HTTP 429`, `cf-mitigated: challenge`) to the app's `httpx` client after a
burst of unthrottled requests — a direct consequence of enabling thirteen
stores with no rate limiting.

This change adds a per-store rate limiter and a bounded retry policy around the
HTTP client every adapter already uses. It does **not** change adapters,
matching, or the orchestrator.

## 2. Goals

- Enforce each store's configured `requests_per_second` on every request the
  app makes to that store.
- Retry transient rejections (`429`, `503`, and network/timeout errors) a
  bounded number of times with exponential backoff and jitter, honouring
  `Retry-After`.
- Surface a clear, distinct error when retries are exhausted, so the UI's store
  error chip says the store is rate-limiting/challenging us rather than a bare
  `429`.
- Keep the change behind the existing `HttpClient` seam so all adapters benefit
  without edits, and keep it unit-testable with no real network or real sleeps.

## 3. Non-goals

- No per-store concurrency plumbing or global aggregate cap (deferred).
- No browser/Playwright fallback for challenged stores.
- No change to caching, parsing, matching, or the SSE protocol.
- No proxy rotation or anti-bot evasion; the identifying `User-Agent` stays.

## 4. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Rate limit + retry/backoff | Directly targets the failure; concurrency/global cap is a separate concern. |
| Seam | Wrapper `HttpClient` per store | Adapters already call `self._http`; one seam covers all thirteen, no adapter edits. |
| Rate model | Minimum interval between calls (`1 / requests_per_second`), thread-safe | Simple, deterministic, easy to test; enough to avoid bursts. |
| Retry budget | 3 attempts total | Enough for genuine short rate limits; bounded latency. |
| Backoff | Exponential, `base 0.5s`, `max 8s`, full jitter | Standard; jitter avoids synchronised retries across stores. |
| `Retry-After` | Honoured, capped at `max_delay` | Respect the store's own signal. |
| Retryable | `429`, `503`, and `status_code is None` (network/timeout) | Transient; other 4xx/5xx are not retried. |
| Challenge signal | Detect `cf-mitigated: challenge`, name it in the error | Explains the opaque 429 the operator actually saw. |
| Branch | Same branch as the cart work (`feature/mtg-card-tracker`) | Per the operator's choice. |

## 5. Architecture

```
app/
  http.py        # HttpError gains status_code / retry_after / challenged
  politeness.py  # NEW: RateLimiter, RetryPolicy, RetryingHttpClient, make_store_client
  adapters/
    registry.py  # build_adapters wraps the base client per store
tests/
  test_http.py
  test_politeness.py   # NEW
  test_registry.py
```

Data flow: `build_adapters(configs, base_http)` wraps `base_http` once per store
in a `RetryingHttpClient` carrying that store's `RateLimiter` and the default
`RetryPolicy`. Adapters receive the wrapper as `self._http` and are otherwise
unchanged. Each `get_json`/`get_text` call waits on the limiter, delegates to the
base client, and retries transient failures.

### 5.1 `app/http.py`

`HttpError` gains three attributes while remaining backward compatible
(they default to `None`/`False`):

```python
class HttpError(Exception):
    def __init__(self, message, status_code=None, retry_after=None, challenged=False):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.challenged = challenged
```

`HttpxClient` populates them on a `>= 400` response: `status_code` from the
response; `retry_after` parsed from the `Retry-After` header (integer/float
seconds, else an HTTP-date, clamped at `>= 0`, `None` if unparseable); and
`challenged = response.headers.get("cf-mitigated", "").lower() == "challenge"`.

### 5.2 `app/politeness.py` (new)

- `RateLimiter(rate_per_second, clock=time.monotonic, sleep=time.sleep)` —
  thread-safe `wait()` enforcing a minimum `1 / rate_per_second` interval
  between calls; the first call is immediate. `rate_per_second <= 0` (or
  `None`) disables limiting (`interval == 0`). Exposes `interval` and is
  injectable so tests use a fake clock.
- `RetryPolicy(attempts=3, base_delay=0.5, max_delay=8.0)` — frozen dataclass
  with `delay_for(attempt, retry_after, rand=random.random)`: `Retry-After`
  wins (capped at `max_delay`), otherwise full jitter over
  `min(max_delay, base_delay * 2 ** (attempt - 1))`.
- `RetryingHttpClient(inner, limiter, policy=RetryPolicy(), sleep=time.sleep,
  rand=random.random)` — implements the `HttpClient` protocol. Exposes
  `rate_per_second` (derived from the limiter). For each call: `limiter.wait()`,
  then delegate; on `HttpError` where `status_code is None` or in
  `{429, 503}`, sleep `delay_for(...)` and retry; otherwise raise. On
  exhaustion, raise an `HttpError` whose message names the rate limit and,
  when `challenged`, the Cloudflare challenge.
- `make_store_client(base_http, config, *, policy=None, sleep=time.sleep,
  clock=time.monotonic, rand=random.random)` — factory building a
  `RetryingHttpClient` with a limiter from `config.requests_per_second`.
- `RETRYABLE_STATUS = frozenset({429, 503})`.

### 5.3 `app/adapters/registry.py`

`build_adapters` constructs `client = make_store_client(http, cfg)` per store
and passes it to the adapter instead of the raw `http`. The browser path is
unchanged apart from receiving the wrapped client.

## 6. Error handling

- Retries never mask a permanent error: `404`, `500`, etc. raise immediately
  (unchanged behaviour).
- Network/timeout errors are retried, then re-raised unchanged on the final
  attempt.
- Exhausted retries raise `HttpError` with `status_code`, `retry_after`, and
  `challenged` preserved; adapters already wrap any exception from the client
  into `AdapterError`, so the existing per-`(card, store)` error isolation and
  SSE error chip keep working, now with a clearer message.

## 7. Testing

- `tests/test_http.py`: `HttpError` carries `status_code`; `Retry-After`
  parsed as seconds and as an HTTP-date, and `None` when absent; the
  `cf-mitigated: challenge` flag is set.
- `tests/test_politeness.py` (new): limiter spacing with a fake clock; retry
  then success on `429`; exhaustion raises with the status and a clear message;
  `Retry-After` overrides backoff; `404`/`500` are not retried; network errors
  are retried; the limiter is waited on before each attempt; jitter is bounded.
- `tests/test_registry.py`: `build_adapters` gives each adapter a
  `RetryingHttpClient`; `make_store_client` reflects the configured rate.
- No real network and no real sleeps: clock, sleep, and rand are injected.

## 8. Behaviour notes / trade-offs

- A cold search now takes a few seconds per store at the default `1.0`
  req/s when a store matches many products (search plus up to ten
  `products/{handle}.js` fetches). The 6-hour cache keeps repeat searches fast.
- A Cloudflare challenge that outlasts the three attempts still surfaces as a
  store error, but now says so explicitly.

## 9. Files touched

- `app/http.py`, `app/politeness.py` (**new**), `app/adapters/registry.py`.
- `tests/test_http.py`, `tests/test_politeness.py` (**new**),
  `tests/test_registry.py`.
- `README.md` — replace the "Politeness config is not wired" limitation with
  the implemented behaviour.

## 10. Open questions / assumptions

- Aggregate cross-store request volume is still unbounded; a future global cap
  may be needed if challenges recur even with per-store limiting.
- `Retry-After` on a challenge response is often absent; the jittered backoff is
  then a best effort, and the final error explains the challenge.
