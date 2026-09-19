import json
from pathlib import Path

import pytest

from app.adapters.base import AdapterError, CardRequest
from app.adapters.registry import StoreConfig
from app.adapters.shopify import ShopifyAdapter
from app.adapters.greyogre import GreyOgreAdapter
from app.http import HttpError

FIXTURES = Path(__file__).parent / "fixtures" / "greyogre"
PRODUCTS = FIXTURES / "suggest_sol_ring.json"
SETS = FIXTURES / "scryfall_sets.json"


class FixtureHttp:
    """Fake client: serves the product payload for search calls and the set
    list for Scryfall /sets calls. Records product calls only."""

    def __init__(self, products):
        self._products = products
        self._sets = json.loads(SETS.read_text())
        self.calls = []

    def get_json(self, url, params=None):
        if url.endswith("/sets"):
            return self._sets
        self.calls.append((url, params))
        return self._products

    def get_text(self, url, params=None):
        return ""


def _config():
    return StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )


def test_search_maps_variants_to_offers():
    http = FixtureHttp(json.loads(PRODUCTS.read_text()))
    adapter = ShopifyAdapter(_config(), http)
    offers = adapter.search(CardRequest(name="Sol Ring", set_code="LTC", collector_number="409"))

    assert len(offers) == 4
    nm, foil, lp, other = offers
    assert nm.store_id == "greyogre"
    assert str(nm.price_sgd) == "4.50"
    assert nm.condition == "NM"
    assert nm.finish == "nonfoil"
    assert nm.in_stock is True
    assert nm.url == "https://www.greyogregames.com/products/sol-ring-409-ltc-commander"

    assert foil.finish == "foil"
    assert str(foil.price_sgd) == "9.00"

    assert lp.condition == "LP"
    assert lp.in_stock is False

    # bracketed set name resolved to a code via the Scryfall set resolver
    assert nm.printing_hint == "ltc:409"

    # offers from the other printing are returned unfiltered — matching.py (Task 9)
    # is responsible for dropping them
    assert other.printing_hint == "ltc:410"
    assert str(other.price_sgd) == "5.00"

    # the search query was the card name
    assert http.calls[0][1]["q"] == "Sol Ring"


def test_greyogre_adapter_is_a_shopify_adapter():
    adapter = GreyOgreAdapter(_config(), FixtureHttp({"resources": {"results": {"products": []}}}))
    assert isinstance(adapter, ShopifyAdapter)
    assert adapter.store_id == "greyogre"
    assert adapter.zone == "singapore"


class RaisingHttp:
    def get_json(self, url, params=None):
        raise HttpError("boom")

    def get_text(self, url, params=None):
        return ""


class ListHttp:
    """Returns a bare JSON list for search — a malformed but non-failing 200."""

    def get_json(self, url, params=None):
        if url.endswith("/sets"):
            return {"data": []}
        return ["not", "a", "dict"]

    def get_text(self, url, params=None):
        return ""


def test_fetch_products_wraps_http_failure_as_adapter_error():
    adapter = ShopifyAdapter(_config(), RaisingHttp())
    with pytest.raises(AdapterError):
        adapter.fetch_products("Sol Ring")


def test_malformed_search_payload_does_not_escape():
    adapter = ShopifyAdapter(_config(), ListHttp())
    assert adapter.search(CardRequest(name="Sol Ring")) == []


def test_null_price_and_variants_are_skipped():
    payload = {"resources": {"results": {"products": [
        {"title": "Sol Ring", "handle": "h1", "url": "/products/h1", "variants": None},
        {"title": "Sol Ring", "handle": "h2", "url": "/products/h2", "variants": [
            {"title": "Near Mint", "price": None},
            {"title": "Foil", "price": "9.00"},
        ]},
    ]}}}
    adapter = ShopifyAdapter(_config(), FixtureHttp(payload))
    offers = adapter.search(CardRequest(name="Sol Ring"))

    assert len(offers) == 1
    assert str(offers[0].price_sgd) == "9.00"
    assert offers[0].finish == "foil"
    assert offers[0].currency == "SGD"
