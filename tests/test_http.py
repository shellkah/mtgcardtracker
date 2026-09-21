import httpx
import pytest

from app.http import HttpError, HttpxClient


def _client(handler):
    return HttpxClient("mtgcardtracker/0.1", transport=httpx.MockTransport(handler))


def test_get_json_ok():
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    assert _client(handler).get_json("https://x.test/a") == {"ok": True}


def test_get_json_raises_on_error_status():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(HttpError):
        _client(handler).get_json("https://x.test/a")


def test_get_text_returns_body():
    def handler(request):
        return httpx.Response(200, text="<html>hi</html>")

    assert _client(handler).get_text("https://x.test/a") == "<html>hi</html>"


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
