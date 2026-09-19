"""Contract tests for the Singapore Shopify stores added after Grey Ogre.

Each case replays live-recorded fixtures (``suggest_sol_ring.json`` +
``product_sol_ring.json``) for one store and asserts the shared ShopifyAdapter
maps variants to offers correctly. The shared Scryfall set fixture contains
``SLD``, so Hideout's Secret Lair printing resolves to an exact hint.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.base import CardRequest
from app.adapters.cardscitadel import CardsCitadelAdapter
from app.adapters.gameshaven import GamesHavenAdapter
from app.adapters.hideout import HideoutAdapter
from app.adapters.registry import StoreConfig, build_adapters, load_config
from app.adapters.shopify import ShopifyAdapter
from app.adapters.shopify_parse import normalize_name

FIXTURES = Path(__file__).parent / "fixtures"
# Reuse Grey Ogre's recorded set list rather than duplicating it per store.
SETS = FIXTURES / "greyogre" / "scryfall_sets.json"


class FixtureHttp:
    """Fake client replaying one store's recorded suggest + product payloads."""

    def __init__(self, store_fixtures: Path):
        self._suggest = json.loads((store_fixtures / "suggest_sol_ring.json").read_text())
        self._product = json.loads((store_fixtures / "product_sol_ring.json").read_text())
        self._sets = json.loads(SETS.read_text())
        self.suggest_calls = []
        self.product_calls = []

    def get_json(self, url, params=None):
        if url.endswith("/sets"):
            return self._sets
        if "/search/suggest.json" in url:
            self.suggest_calls.append((url, params))
            return self._suggest
        if url.endswith(".js"):
            self.product_calls.append((url, params))
            return self._product
        raise AssertionError(f"unexpected URL: {url}")

    def get_text(self, url, params=None):
        return ""


CASES = [
    pytest.param(
        {
            "id": "gameshaven",
            "name": "Games Haven",
            "base_url": "https://www.gameshaventcg.com",
            "adapter": GamesHavenAdapter,
            "handle": "sol-ring-fallout",
            "hint": None,
            "offers": {
                ("NM", "nonfoil"): ("4.10", False),
                ("NM", "foil"): ("8.50", True),
                ("LP", "nonfoil"): ("3.50", False),
                ("DMG", "nonfoil"): ("0.10", False),
                ("DMG", "foil"): ("0.10", False),
            },
        },
        id="gameshaven",
    ),
    pytest.param(
        {
            "id": "cardscitadel",
            "name": "Cards Citadel",
            "base_url": "https://cardscitadel.com",
            "adapter": CardsCitadelAdapter,
            "handle": "sol-ring-commander-collection-green",
            "hint": None,
            "offers": {
                ("NM", "nonfoil"): ("14.10", True),
                ("LP", "nonfoil"): ("13.40", False),
                ("NM", "foil"): ("39.20", True),
            },
        },
        id="cardscitadel",
    ),
    pytest.param(
        {
            "id": "hideout",
            "name": "Hideout",
            "base_url": "https://hideoutcg.com",
            "adapter": HideoutAdapter,
            "handle": "sol-ring-1011-secret-lair-drop-series",
            "hint": "sld:1011",
            "offers": {
                ("NM", "nonfoil"): ("30.00", False),
                ("HP", "nonfoil"): ("22.50", False),
                ("NM", "foil"): ("33.50", True),
            },
        },
        id="hideout",
    ),
]


def _config(case):
    return StoreConfig(
        id=case["id"], name=case["name"], zone="singapore",
        adapter_path=f"app.adapters.{case['id']}:Adapter",
        base_url=case["base_url"],
    )


@pytest.mark.parametrize("case", CASES)
def test_store_maps_recorded_shopify_variants(case):
    http = FixtureHttp(FIXTURES / case["id"])
    offers = case["adapter"](_config(case), http).search(CardRequest(name="Sol Ring"))

    assert offers

    chosen = [o for o in offers if case["handle"] in o.url]
    assert chosen
    for (condition, finish), (price, in_stock) in case["offers"].items():
        offer = next(
            o for o in chosen if o.condition == condition and o.finish == finish
        )
        assert offer.price_sgd == Decimal(price)
        assert offer.in_stock is in_stock
        assert offer.currency == "SGD"
        assert offer.store_id == case["id"]
        assert offer.store_name == case["name"]
        assert offer.url.startswith(f"{case['base_url']}/products/{case['handle']}")

    # every offer carries the chosen product's printing hint (the harness serves
    # the same .js payload for each handle)
    assert {o.printing_hint for o in offers} == {case["hint"]}

    # the full condition/finish vocabulary the stores expose is represented
    assert {o.condition for o in offers} == {"NM", "LP", "MP", "HP", "DMG"}
    assert {o.finish for o in offers} == {"nonfoil", "foil"}

    # only name-matching products are fetched for variants
    suggest_products = http._suggest["resources"]["results"]["products"]
    wanted = [
        p["handle"] for p in suggest_products
        if normalize_name(p["title"]) == "sol ring"
    ]
    requested = [url.rsplit("/", 1)[-1][: -len(".js")] for url, _ in http.product_calls]
    assert requested == wanted
    assert case["handle"] in requested
    assert http.suggest_calls[0][1]["q"] == "Sol Ring"


def test_new_stores_are_enabled_and_shopify_backed():
    configs = {c.id: c for c in load_config(Path("config/stores.yaml"))}
    for store_id in ("greyogre", "gameshaven", "cardscitadel", "hideout"):
        cfg = configs[store_id]
        assert cfg.enabled is True
        assert cfg.zone == "singapore"
    adapters = build_adapters(configs.values(), FixtureHttp(FIXTURES / "gameshaven"))
    by_id = {a.store_id: a for a in adapters}
    for store_id in ("gameshaven", "cardscitadel", "hideout"):
        assert isinstance(by_id[store_id], ShopifyAdapter)
