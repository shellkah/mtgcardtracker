from app.sets import SetResolver, normalize_set_name


class FakeHttp:
    def __init__(self, payload=None, raise_error=False):
        self._payload = payload or {}
        self._raise = raise_error
        self.calls = 0

    def get_json(self, url, params=None):
        self.calls += 1
        if self._raise:
            raise RuntimeError("boom")
        return self._payload

    def get_text(self, url, params=None):
        return ""


SETS = {"data": [{"code": "LTC", "name": "The Lord of the Rings: Tales of Middle-Earth Commander"}]}


def test_normalize_set_name_collapses_whitespace_and_case():
    assert normalize_set_name("  The   Lord of the Rings ") == "the lord of the rings"


def test_code_for_resolves_scryfall_name_to_lowercase_code():
    resolver = SetResolver(FakeHttp(SETS))
    assert resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander") == "ltc"


def test_code_for_returns_none_for_unknown_name():
    resolver = SetResolver(FakeHttp(SETS))
    assert resolver.code_for("Some Unknown Set") is None


def test_code_for_returns_none_on_http_error():
    resolver = SetResolver(FakeHttp(raise_error=True))
    assert resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander") is None


def test_sets_are_fetched_once():
    http = FakeHttp(SETS)
    resolver = SetResolver(http)
    resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander")
    resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander")
    assert http.calls == 1
