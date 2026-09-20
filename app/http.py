from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx


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


class HttpClient(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        """Return the decoded JSON payload unmodified (object or array)."""
        ...

    def get_text(self, url: str, params: dict | None = None) -> str:
        ...


class HttpxClient:
    def __init__(self, user_agent: str, timeout: float = 15.0, transport=None):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            transport=transport,
        )

    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise HttpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            _raise_for_status(url, response)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} did not return JSON") from exc

    def get_text(self, url: str, params: dict | None = None) -> str:
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise HttpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            _raise_for_status(url, response)
        return response.text

    def close(self) -> None:
        self._client.close()
