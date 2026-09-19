import json
from pathlib import Path

from app.adapters.base import CardRequest
from app.adapters.registry import StoreConfig
from app.adapters.shopify import ShopifyAdapter
from app.adapters.greyogre import GreyOgreAdapter

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

    assert len(offers) == 3
    nm = offers[0]
    assert nm.store_id == "greyogre"
    assert str(nm.price_sgd) == "4.50"
    assert nm.condition == "NM"
    assert nm.finish == "nonfoil"
    assert nm.in_stock is True
    assert nm.url == "https://www.greyogregames.com/products/sol-ring-409-ltc-commander"

    foil = offers[1]
    assert foil.finish == "foil"
    assert str(foil.price_sgd) == "9.00"

    lp = offers[2]
    assert lp.condition == "LP"
    assert lp.in_stock is False

    # bracketed set name resolved to a code via the Scryfall set resolver
    assert nm.printing_hint == "ltc:409"

    # the search query was the card name
    assert http.calls[0][1]["q"] == "Sol Ring"


def test_greyogre_adapter_is_a_shopify_adapter():
    adapter = GreyOgreAdapter(_config(), FixtureHttp({"resources": {"results": {"products": []}}}))
    assert isinstance(adapter, ShopifyAdapter)
    assert adapter.store_id == "greyogre"
    assert adapter.zone == "singapore"
