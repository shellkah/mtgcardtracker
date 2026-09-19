from app.http import HttpError
from app.scryfall import ScryfallClient


class FakeHttp:
    def __init__(self, json_payload=None, raise_error=False):
        self._payload = json_payload or {}
        self._raise = raise_error

    def get_json(self, url, params=None):
        if self._raise:
            raise HttpError("boom")
        return self._payload

    def get_text(self, url, params=None):
        return ""


def test_validate_printing_true_when_card_returned():
    client = ScryfallClient(FakeHttp({"set": "ltc", "collector_number": "409"}))
    assert client.validate_printing("LTC", "409") is True


def test_validate_printing_false_on_error():
    client = ScryfallClient(FakeHttp(raise_error=True))
    assert client.validate_printing("LTC", "409") is False


def test_find_printings_maps_set_and_number():
    payload = {"data": [{"set": "LTC", "collector_number": "409"}]}
    assert ScryfallClient(FakeHttp(payload)).find_printings("Sol Ring") == [
        {"set": "ltc", "collector_number": "409"}
    ]


def test_find_printings_empty_on_error():
    assert ScryfallClient(FakeHttp(raise_error=True)).find_printings("Sol Ring") == []
