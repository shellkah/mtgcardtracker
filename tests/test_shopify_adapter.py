import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.base import AdapterError, CardRequest
from app.adapters.registry import StoreConfig
from app.adapters.shopify import ShopifyAdapter
from app.adapters.greyogre import GreyOgreAdapter
from app.http import HttpError

FIXTURES = Path(__file__).parent / "fixtures" / "greyogre"
# Real search/suggest.json payload: products always carry an empty variants list.
SUGGEST = FIXTURES / "suggest_sol_ring.json"
# Real products/{handle}.js payload: variant titles, integer-cent prices, stock.
PRODUCT = FIXTURES / "product_sol_ring.json"
SETS = FIXTURES / "scryfall_sets.json"

CHOSEN_HANDLE = "sol-ring-2467-secret-lair-drop-series"


def _load(path):
    return json.loads(path.read_text())


class FixtureHttp:
    """Fake client routing by URL.

    - ``/search/suggest.json`` -> the recorded suggest payload
    - any URL ending ``.js`` -> the recorded product payload (optionally a
      callable ``product(url)`` to vary per handle)
    - any URL ending ``/sets`` -> the Scryfall set list

    Records product-search calls and per-product ``.js`` calls separately.
    """

    def __init__(self, suggest, product):
        self._suggest = suggest
        self._product = product
        self._sets = _load(SETS)
        self.suggest_calls = []
        self.product_calls = []

    def _resolve_product(self, url):
        if callable(self._product):
            return self._product(url)
        return self._product

    def get_json(self, url, params=None):
        if url.endswith("/sets"):
            return self._sets
        if "/search/suggest.json" in url:
            self.suggest_calls.append((url, params))
            return self._suggest
        if url.endswith(".js"):
            self.product_calls.append((url, params))
            return self._resolve_product(url)
        raise AssertionError(f"unexpected URL: {url}")

    def get_text(self, url, params=None):
        return ""


def _config():
    return StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )


def test_search_maps_real_variants_to_offers():
    http = FixtureHttp(_load(SUGGEST), _load(PRODUCT))
    adapter = ShopifyAdapter(_config(), http)
    offers = adapter.search(CardRequest(name="Sol Ring", set_code="SLD", collector_number="2467"))

    assert offers  # the real suggest endpoint has no variants; .js supplies them

    # the harness serves one real .js fixture for every handle, so identify the
    # chosen product by its own suggest URL
    chosen = [o for o in offers if CHOSEN_HANDLE in o.url]
    assert chosen
    nm = next(o for o in chosen if o.condition == "NM" and o.finish == "nonfoil")
    assert nm.store_id == "greyogre"
    assert nm.condition == "NM"
    assert nm.finish == "nonfoil"
    assert nm.in_stock is True
    assert nm.currency == "SGD"
    # integer cents from Shopify (1525) become dollars (15.25)
    assert nm.price_sgd == Decimal("15.25")
    assert str(nm.price_sgd) == "15.25"
    assert nm.url.startswith(
        "https://www.greyogregames.com/products/sol-ring-2467-secret-lair-drop-series"
    )

    foil = next(o for o in chosen if o.condition == "NM" and o.finish == "foil")
    assert foil.price_sgd == Decimal("15.25")
    assert foil.in_stock is True

    # a played, out-of-stock non-foil offer survives the mapping too
    lp = next(o for o in chosen if o.condition == "LP" and o.finish == "nonfoil")
    assert lp.price_sgd == Decimal("12.25")
    assert lp.in_stock is False

    # both finishes and the full condition vocabulary are represented
    assert {o.finish for o in offers} >= {"foil", "nonfoil"}
    assert {o.condition for o in offers} >= {"NM", "LP", "MP", "HP", "DMG"}

    # the bracketed set name resolved to a code via the Scryfall set resolver
    assert all(o.printing_hint == "sld:2467" for o in offers)


def test_name_filter_does_not_print_filter():
    http = FixtureHttp(_load(SUGGEST), _load(PRODUCT))
    adapter = ShopifyAdapter(_config(), http)
    offers = adapter.search(CardRequest(name="Sol Ring", set_code="SLD", collector_number="2467"))

    suggest_products = _load(SUGGEST)["resources"]["results"]["products"]
    wanted = [p["handle"] for p in suggest_products]

    # every name-matching product was queried for its variants, regardless of
    # the requested printing — matching.py drops the wrong printings later
    requested = [url.rsplit("/", 1)[-1][: -len(".js")] for url, _ in http.product_calls]
    assert requested == wanted
    assert CHOSEN_HANDLE in requested
    assert any(h != CHOSEN_HANDLE for h in requested)
    # harness serves the same .js fixture for every handle, so every product
    # contributes the fixture's 10 variants
    assert len(offers) == len(suggest_products) * len(_load(PRODUCT)["variants"])

    assert http.suggest_calls[0][1]["q"] == "Sol Ring"


def test_product_url_builds_js_endpoint():
    adapter = ShopifyAdapter(_config(), FixtureHttp({}, {}))
    assert adapter.product_url("sol-ring") == "https://www.greyogregames.com/products/sol-ring.js"


def test_greyogre_adapter_is_a_shopify_adapter():
    http = FixtureHttp({"resources": {"results": {"products": []}}}, {})
    adapter = GreyOgreAdapter(_config(), http)
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


def test_fetch_variants_wraps_http_failure_as_adapter_error():
    adapter = ShopifyAdapter(_config(), RaisingHttp())
    with pytest.raises(AdapterError):
        adapter.fetch_variants("sol-ring")


def test_malformed_search_payload_does_not_escape():
    adapter = ShopifyAdapter(_config(), ListHttp())
    assert adapter.search(CardRequest(name="Sol Ring")) == []


def test_null_price_and_null_variants_are_skipped():
    suggest = {"resources": {"results": {"products": [
        {"title": "Sol Ring", "handle": "h1", "url": "/products/h1"},
        {"title": "Sol Ring", "handle": "h2", "url": "/products/h2"},
    ]}}}

    def product(url):
        if url.endswith("/products/h1.js"):
            return {"title": "Sol Ring", "handle": "h1", "variants": None}
        return {"title": "Sol Ring", "handle": "h2", "variants": [
            {"title": "Near Mint", "price": None},
            {"title": "Near Mint Foil", "price": 900},
        ]}

    adapter = ShopifyAdapter(_config(), FixtureHttp(suggest, product))
    offers = adapter.search(CardRequest(name="Sol Ring"))

    assert len(offers) == 1
    assert offers[0].price_sgd == Decimal("9.00")
    assert offers[0].finish == "foil"
    assert offers[0].currency == "SGD"


def test_variant_fetch_failure_skips_only_that_product():
    suggest = {"resources": {"results": {"products": [
        {"title": "Sol Ring", "handle": "broken", "url": "/products/broken"},
        {"title": "Sol Ring", "handle": "good", "url": "/products/good"},
    ]}}}

    def product(url):
        if url.endswith("/products/broken.js"):
            raise HttpError("boom")
        return {"title": "Sol Ring", "handle": "good", "variants": [
            {"title": "Near Mint", "price": 500, "available": True},
        ]}

    adapter = ShopifyAdapter(_config(), FixtureHttp(suggest, product))
    offers = adapter.search(CardRequest(name="Sol Ring"))

    assert len(offers) == 1
    assert offers[0].price_sgd == Decimal("5.00")
