import pytest
from curl_cffi import requests as curl_requests

from app.curlclient import CurlCffiClient
from app.http import HttpError


class _Response:
    def __init__(self, status_code=200, headers=None, text="", json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        pass


def _client(response=None, error=None):
    session = _Session(response, error)
    return CurlCffiClient(session=session), session


def test_get_json_parses_body():
    client, session = _client(_Response(json_data={"ok": True}, text='{"ok": true}'))
    assert client.get_json("https://x.test/a", {"q": "x"}) == {"ok": True}
    assert session.calls[0]["params"] == {"q": "x"}


def test_get_text_returns_body():
    client, _ = _client(_Response(text="<html>hi</html>"))
    assert client.get_text("https://x.test/a") == "<html>hi</html>"


def test_error_carries_status_and_challenge_flag():
    client, _ = _client(
        _Response(status_code=429, headers={"cf-mitigated": "challenge"})
    )
    with pytest.raises(HttpError) as excinfo:
        client.get_json("https://x.test/a")
    assert excinfo.value.status_code == 429
    assert excinfo.value.challenged is True


def test_network_error_becomes_http_error():
    client, _ = _client(error=curl_requests.errors.RequestsError("boom"))
    with pytest.raises(HttpError) as excinfo:
        client.get_text("https://x.test/a")
    assert excinfo.value.status_code is None


def test_non_json_body_raises():
    client, _ = _client(_Response(text="nope"))
    with pytest.raises(HttpError):
        client.get_json("https://x.test/a")
