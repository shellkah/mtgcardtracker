"""Browser-fingerprint HttpClient using curl_cffi.

Cloudflare frequently blocks ``httpx`` on TLS/JA3 fingerprint alone: the store
answers ``429`` with ``cf-mitigated: challenge`` and never issues a clearance
cookie, so replaying cookies cannot help. curl_cffi replays a genuine Chrome
ClientHello and header set, which passes those checks in well under a second.

It is the app's primary client. Byparr (a full headless browser) is kept as the
last-resort fallback for challenges that fingerprint impersonation cannot clear.
"""

from __future__ import annotations

from curl_cffi import requests as curl_requests

from app.http import HttpError, _raise_for_status


class CurlCffiClient:
    """HttpClient that impersonates a real browser's TLS fingerprint."""

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        timeout: float = 20.0,
        session=None,
    ):
        self._session = session or curl_requests.Session(impersonate=impersonate)
        self._timeout = timeout

    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        response = self._get(url, params)
        try:
            return response.json()
        except Exception as exc:
            raise HttpError(f"{url} did not return JSON") from exc

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self._get(url, params).text

    def _get(self, url: str, params: dict | None):
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except curl_requests.errors.RequestsError as exc:
            raise HttpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            _raise_for_status(url, response)
        return response

    def close(self) -> None:
        self._session.close()
