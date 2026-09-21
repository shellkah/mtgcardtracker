"""Byparr-backed HttpClient: clear anti-bot challenges with a real browser.

Byparr (https://github.com/ThePhaseless/Byparr) is a FlareSolverr-compatible
solver. It drives a headless Firefox to load a URL, solves the Cloudflare (or
other provider's) challenge, and returns the final page body over a simple
HTTP API::

    POST {base_url}/v1
    {"cmd": "request.get", "url": "https://store/...", "maxTimeout": 60}

    -> {"status": "ok", "solution": {"status": 200, "response": "<html>…"}}

Two quirks matter here:

* On success Byparr reports ``solution.status == 200`` regardless of the
  target's own status code; failures raise an HTTP 408/502 from Byparr itself.
* Firefox renders non-HTML bodies through its plaintext viewer, so JSON and
  text payloads arrive wrapped in ``<pre>…</pre>`` with HTML entities escaped.

:class:`ByparrClient` implements the same :class:`~app.http.HttpClient`
protocol as :class:`~app.http.HttpxClient`, so it can be composed with the
existing per-store politeness wrapper or used as a fallback.
"""

from __future__ import annotations

import html as _html
import json
import re

import httpx

from app.http import HttpError

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL)

#: Statuses that commonly signal an unsolved challenge / bot block.
CHALLENGE_STATUS = frozenset({403, 429, 503})


def extract_document(body: str) -> str:
    """Return a body with Firefox's plaintext-viewer wrapper removed.

    JSON/text responses come back as ``<html>…<pre>{…}</pre>…</html>``. HTML
    responses are returned unchanged.
    """
    match = _PRE_RE.search(body)
    return _html.unescape(match.group(1)) if match else body


class ByparrClient:
    """HttpClient that resolves every URL through Byparr's browser solver."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8191",
        *,
        timeout: float = 90.0,
        max_timeout: int = 60,
        transport=None,
    ):
        self._base_url = base_url.rstrip("/")
        self._max_timeout = max_timeout
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def _fetch(self, url: str, params: dict | None) -> str:
        target = str(httpx.URL(url, params=params)) if params else url
        try:
            response = self._client.post(
                f"{self._base_url}/v1",
                json={
                    "cmd": "request.get",
                    "url": target,
                    "maxTimeout": self._max_timeout,
                },
            )
        except httpx.HTTPError as exc:
            raise HttpError(f"byparr request failed for {url}: {exc}") from exc
        if response.status_code >= 400:
            raise HttpError(
                f"byparr returned {response.status_code} for {url}",
                status_code=response.status_code,
                challenged=response.status_code in CHALLENGE_STATUS,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HttpError(f"byparr did not return JSON for {url}") from exc
        if payload.get("status") != "ok":
            raise HttpError(
                f"byparr could not solve {url}: {payload.get('message', 'error')}"
            )
        solution = payload.get("solution") or {}
        return extract_document(solution.get("response") or "")

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self._fetch(url, params)

    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        body = self._fetch(url, params)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise HttpError(f"{url} did not return JSON via byparr") from exc

    def close(self) -> None:
        self._client.close()
