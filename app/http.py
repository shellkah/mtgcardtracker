from __future__ import annotations

from typing import Protocol

import httpx


class HttpError(Exception):
    pass


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
            raise HttpError(f"{url} returned {response.status_code}")
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
            raise HttpError(f"{url} returned {response.status_code}")
        return response.text

    def close(self) -> None:
        self._client.close()
