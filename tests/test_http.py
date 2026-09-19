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
