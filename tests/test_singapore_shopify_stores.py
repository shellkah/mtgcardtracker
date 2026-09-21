"""Contract tests for the Singapore Shopify stores added after Grey Ogre.

Each case replays live-recorded fixtures (``suggest_sol_ring.json`` +
``product_sol_ring.json``) for one store and asserts the store's adapter maps
variants to offers correctly. The shared Scryfall set fixture contains ``SLD``,
so Secret Lair printings resolve to an exact hint.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.arcanesanctum import ArcaneSanctumAdapter
from app.adapters.base import CardRequest
from app.adapters.cardaffinity import CardAffinityAdapter
from app.adapters.cardscitadel import CardsCitadelAdapter
from app.adapters.fivemana import FiveManaAdapter
from app.adapters.flagship import FlagshipAdapter
from app.adapters.fyendalhobby import FyendalHobbyAdapter
from app.adapters.gameshaven import GamesHavenAdapter
from app.adapters.hideout import HideoutAdapter
from app.adapters.hideyoshi import HideyoshiAdapter
from app.adapters.manapro import ManaProAdapter
from app.adapters.mtgasia import MtgAsiaAdapter
from app.adapters.onemtg import OneMtgAdapter
from app.adapters.registry import StoreConfig, build_adapters, load_config
from app.adapters.shopify import ShopifyAdapter

FIXTURES = Path(__file__).parent / "fixtures"
# Reuse Grey Ogre's recorded set list rather than duplicating it per store.
SETS = FIXTURES / "greyogre" / "scryfall_sets.json"

FULL_CONDITIONS = {"NM", "LP", "MP", "HP", "DMG"}
FULL_FINISHES = {"nonfoil", "foil"}


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
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
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
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
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
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("30.00", False),
                ("HP", "nonfoil"): ("22.50", False),
                ("NM", "foil"): ("33.50", True),
            },
        },
        id="hideout",
    ),
    pytest.param(
        {
            "id": "onemtg",
            "name": "One MTG",
            "base_url": "https://onemtg.com.sg",
            "adapter": OneMtgAdapter,
            "handle": "sol-ring-borderless-alternate-art-commander-masters",
            "hint": None,
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("18.00", True),
                ("LP", "nonfoil"): ("12.80", False),
                ("MP", "nonfoil"): ("11.40", False),
                ("HP", "nonfoil"): ("10.00", False),
                ("DMG", "nonfoil"): ("8.50", False),
                ("NM", "foil"): ("38.70", False),
                ("LP", "foil"): ("34.80", False),
                ("MP", "foil"): ("30.90", False),
                ("HP", "foil"): ("27.10", False),
                ("DMG", "foil"): ("23.20", False),
            },
        },
        id="onemtg",
    ),
    pytest.param(
        {
            "id": "mtgasia",
            "name": "MTG Asia",
            "base_url": "https://www.mtg-asia.com",
            "adapter": MtgAsiaAdapter,
            "handle": "sol-ring-1011-secret-lair-drop-series",
            "hint": "sld:1011",
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("30.00", True),
                ("LP", "nonfoil"): ("27.00", False),
                ("MP", "nonfoil"): ("24.00", False),
                ("HP", "nonfoil"): ("21.00", False),
                ("DMG", "nonfoil"): ("15.00", False),
                ("NM", "foil"): ("33.40", False),
                ("LP", "foil"): ("30.10", False),
                ("MP", "foil"): ("26.70", False),
                ("HP", "foil"): ("23.40", False),
                ("DMG", "foil"): ("16.70", False),
            },
        },
        id="mtgasia",
    ),
    pytest.param(
        {
            "id": "manapro",
            "name": "Mana Pro",
            "base_url": "https://sg-manapro.com",
            "adapter": ManaProAdapter,
            "handle": "sol-ring-borderless-fallout",
            "hint": None,
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("54.80", True),
                ("LP", "nonfoil"): ("49.30", False),
                ("MP", "nonfoil"): ("43.90", False),
                ("HP", "nonfoil"): ("38.40", False),
                ("DMG", "nonfoil"): ("32.90", False),
                ("NM", "foil"): ("75.40", True),
                ("LP", "foil"): ("67.90", False),
                ("MP", "foil"): ("60.40", False),
                ("HP", "foil"): ("52.80", False),
                ("DMG", "foil"): ("45.30", False),
            },
        },
        id="manapro",
    ),
    pytest.param(
        {
            "id": "cardaffinity",
            "name": "Card Affinity",
            "base_url": "https://card-affinity.com",
            "adapter": CardAffinityAdapter,
            "handle": "sol-ring-2807-secret-lair-drop-series",
            "hint": "sld:2807",
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("11.70", True),
                ("LP", "nonfoil"): ("11.20", False),
                ("MP", "nonfoil"): ("10.00", False),
                ("HP", "nonfoil"): ("8.80", False),
                ("DMG", "nonfoil"): ("8.20", False),
                ("NM", "foil"): ("16.70", True),
                ("LP", "foil"): ("15.90", False),
                ("MP", "foil"): ("14.20", False),
                ("HP", "foil"): ("12.60", False),
                ("DMG", "foil"): ("11.70", False),
            },
        },
        id="cardaffinity",
    ),
    pytest.param(
        {
            "id": "hideyoshi",
            "name": "Hideyoshi TCG",
            "base_url": "https://hideyoshitcg.com",
            "adapter": HideyoshiAdapter,
            "handle": "sol-ring-2683-secret-lair-drop-series",
            "hint": "sld:2683",
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("11.10", False),
                ("LP", "nonfoil"): ("10.50", False),
                ("MP", "nonfoil"): ("9.40", False),
                ("HP", "nonfoil"): ("8.30", False),
                ("DMG", "nonfoil"): ("7.80", False),
                ("NM", "foil"): ("15.10", True),
                ("LP", "foil"): ("14.40", False),
                ("MP", "foil"): ("12.80", False),
                ("HP", "foil"): ("11.30", False),
                ("DMG", "foil"): ("10.60", False),
            },
        },
        id="hideyoshi",
    ),
    pytest.param(
        {
            "id": "flagship",
            "name": "Flagship Games",
            "base_url": "https://www.flagshipgames.sg",
            "adapter": FlagshipAdapter,
            "handle": "sol-ring-secret-lair-drop-series-1",
            "hint": "sld:1011",
            "conditions": FULL_CONDITIONS,
            "finishes": FULL_FINISHES,
            "offers": {
                ("NM", "nonfoil"): ("30.00", False),
                ("LP", "nonfoil"): ("27.00", False),
                ("MP", "nonfoil"): ("21.00", False),
                ("HP", "nonfoil"): ("18.00", False),
                ("DMG", "nonfoil"): ("15.00", False),
                ("NM", "foil"): ("33.40", True),
                ("LP", "foil"): ("30.10", False),
                ("MP", "foil"): ("23.40", False),
                ("HP", "foil"): ("20.10", False),
                ("DMG", "foil"): ("16.70", False),
            },
        },
        id="flagship",
    ),
    pytest.param(
        {
            "id": "fivemana",
            "name": "5 Mana",
            "base_url": "https://5-mana.sg",
            "adapter": FiveManaAdapter,
            "handle": "sol-ring-commander-2019",
            "hint": None,
            "conditions": {"NM"},
            "finishes": {"nonfoil"},
            "offers": {
                ("NM", "nonfoil"): ("3.50", True),
            },
        },
        id="fivemana",
    ),
    pytest.param(
        {
            "id": "fyendalhobby",
            "name": "Fyendal Hobby",
            "base_url": "https://fyendalhobby.com",
            "adapter": FyendalHobbyAdapter,
            "handle": "sol-ring",
            "hint": None,
            "conditions": {None},
            "finishes": {"nonfoil"},
            "offers": {
                (None, "nonfoil"): ("11.00", True),
            },
        },
        id="fyendalhobby",
    ),
    pytest.param(
        {
            "id": "arcanesanctum",
            "name": "Arcane Sanctum",
            "base_url": "https://arcanesanctumtcg.com",
            "adapter": ArcaneSanctumAdapter,
            "handle": "sol-ring-nm-foil-judge-gift-cards-2005",
            "hint": None,
            "conditions": {"NM"},
            "finishes": {"foil"},
            "offers": {
                ("NM", "foil"): ("1000.00", True),
            },
        },
        id="arcanesanctum",
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
    adapter = case["adapter"](_config(case), http)
    offers = adapter.search(CardRequest(name="Sol Ring"))

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

    # the condition/finish vocabulary this store exposes is represented
    assert {o.condition for o in offers} == case["conditions"]
    assert {o.finish for o in offers} == case["finishes"]

    # only name-matching products are fetched for variants
    suggest_products = http._suggest["resources"]["results"]["products"]
    wanted = [
        p["handle"] for p in suggest_products
        if adapter._product_key(p["title"]) == "sol ring"
    ]
    requested = [url.rsplit("/", 1)[-1][: -len(".js")] for url, _ in http.product_calls]
    assert requested == wanted
    assert case["handle"] in requested
    assert http.suggest_calls[0][1]["q"] == "Sol Ring"


def test_new_stores_are_enabled_and_shopify_backed():
    configs = {c.id: c for c in load_config(Path("config/stores.yaml"))}
    new_stores = (
        "greyogre", "gameshaven", "cardscitadel", "hideout", "onemtg", "mtgasia",
        "manapro", "cardaffinity", "hideyoshi", "flagship", "fivemana",
        "fyendalhobby", "arcanesanctum",
    )
    for store_id in new_stores:
        cfg = configs[store_id]
        assert cfg.enabled is True
        assert cfg.zone == "singapore"
    adapters = build_adapters(configs.values(), FixtureHttp(FIXTURES / "gameshaven"))
    by_id = {a.store_id: a for a in adapters}
    for store_id in new_stores:
        assert isinstance(by_id[store_id], ShopifyAdapter)
