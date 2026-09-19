import httpx
import pytest

from app.devtools import detect_platform
from app.http import HttpxClient


def _http(handler):
    return HttpxClient("mtgcardtracker/0.1", transport=httpx.MockTransport(handler))


def test_detect_shopify_from_products_json():
    def handler(request):
        return httpx.Response(200, json={"products": []})

    assert detect_platform("https://x.test", _http(handler)) == "shopify"


def test_detect_other_for_bare_list():
    payload = [{"id": 1, "url": "https://x.test/products/a.json"}]

    def handler(request):
        return httpx.Response(200, json=payload)

    assert detect_platform("https://x.test", _http(handler)) == "other"


def test_detect_custom_when_not_json():
    def handler(request):
        return httpx.Response(200, text="<html></html>")

    assert detect_platform("https://x.test", _http(handler)) == "custom"


def test_detect_custom_on_404():
    def handler(request):
        return httpx.Response(404)

    assert detect_platform("https://x.test", _http(handler)) == "custom"
