import json

import httpx
import pytest

from app.byparr import ByparrClient, extract_document
from app.http import FallbackHttpClient, HttpError


def _byparr(handler):
    return ByparrClient(transport=httpx.MockTransport(handler))


def _ok(body, content_type="application/json"):
    return httpx.Response(
        200,
        json={
            "status": "ok",
            "message": "Success",
            "solution": {"url": "https://x.test/a", "status": 200,
                         "contentType": content_type, "response": body},
        },
    )


def test_extract_document_unwraps_firefox_pre_and_unescapes():
    wrapped = "<html><body><pre>{&quot;a&quot;: 1 &amp; 2}</pre></body></html>"
    assert extract_document(wrapped) == '{"a": 1 & 2}'


def test_extract_document_leaves_html_untouched():
    assert extract_document("<html><body>hi</body></html>") == "<html><body>hi</body></html>"


def test_byparr_posts_request_and_decodes_wrapped_json():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok('<pre>{"resources": {"results": []}}</pre>')

    client = _byparr(handler)
    payload = client.get_json("https://x.test/search", {"q": "Sol Ring"})
    assert payload == {"resources": {"results": []}}
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:8191/v1"
    assert seen["body"]["cmd"] == "request.get"
    assert seen["body"]["url"] == "https://x.test/search?q=Sol%20Ring"


def test_byparr_get_text_returns_raw_html():
    client = _byparr(lambda request: _ok("<html><body>ok</body></html>", "text/html"))
    assert client.get_text("https://x.test/a") == "<html><body>ok</body></html>"


def test_byparr_raises_on_error_status():
    def handler(request):
        return httpx.Response(200, json={"status": "error", "message": "Invalid request"})

    with pytest.raises(HttpError) as excinfo:
        _byparr(handler).get_json("https://x.test/a")
    assert "Invalid request" in str(excinfo.value)


def test_byparr_http_error_marks_challenge_status():
    with pytest.raises(HttpError) as excinfo:
        _byparr(lambda request: httpx.Response(408)).get_text("https://x.test/a")
    assert excinfo.value.status_code == 408

    with pytest.raises(HttpError) as excinfo:
        _byparr(lambda request: httpx.Response(502)).get_text("https://x.test/a")
    assert excinfo.value.status_code == 502


def test_byparr_non_json_body_raises():
    client = _byparr(lambda request: _ok("not json", "text/html"))
    with pytest.raises(HttpError):
        client.get_json("https://x.test/a")


class _Primary:
    def __init__(self, error=None, value=None):
        self.error = error
        self.value = value
        self.calls = 0

    def get_json(self, url, params=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value

    get_text = get_json


class _Fallback:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def get_json(self, url, params=None):
        self.calls += 1
        return self.value

    get_text = get_json


def _error(status=None, challenged=False):
    return HttpError("boom", status_code=status, challenged=challenged)


def test_fallback_used_on_cloudflare_challenge():
    primary = _Primary(error=_error(429, challenged=True))
    fallback = _Fallback({"ok": True})
    assert FallbackHttpClient(primary, fallback).get_json("https://x.test/a") == {"ok": True}
    assert primary.calls == 1 and fallback.calls == 1


def test_fallback_used_on_blocked_status():
    primary = _Primary(error=_error(403))
    fallback = _Fallback("solved")
    assert FallbackHttpClient(primary, fallback).get_text("https://x.test/a") == "solved"


def test_fallback_not_used_on_404():
    primary = _Primary(error=_error(404))
    fallback = _Fallback("should not happen")
    with pytest.raises(HttpError):
        FallbackHttpClient(primary, fallback).get_json("https://x.test/a")
    assert fallback.calls == 0


def test_fallback_failure_preserves_original_challenge():
    primary = _Primary(error=_error(429, challenged=True))
    fallback = _Primary(error=HttpError("byparr request failed: connection refused"))
    with pytest.raises(HttpError) as excinfo:
        FallbackHttpClient(primary, fallback).get_json("https://x.test/a")
    assert excinfo.value.status_code == 429
    assert excinfo.value.challenged is True
    assert "browser fallback failed" in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)


def test_fallback_not_used_when_primary_succeeds():
    primary = _Primary(value={"ok": True})
    fallback = _Fallback("should not happen")
    assert FallbackHttpClient(primary, fallback).get_json("https://x.test/a") == {"ok": True}
    assert fallback.calls == 0
