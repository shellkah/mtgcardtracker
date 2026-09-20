from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.base import CardRequest, Offer


def test_card_key_uses_printing_when_present():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    assert card.key == "ltc:409"


def test_card_key_falls_back_to_normalized_name():
    card = CardRequest(name="  Sol Ring  ")
    assert card.key == "sol ring"


def test_offer_to_dict_serializes_decimal_and_datetime():
    offer = Offer(
        store_id="greyogre",
        store_name="Grey Ogre Games",
        title="Sol Ring (409) [LTC]",
        price_sgd=Decimal("4.50"),
        currency="SGD",
        condition="NM",
        finish="nonfoil",
        in_stock=True,
        url="https://example.com/p/sol-ring",
        printing_hint="ltc:409",
        variant_id="41984399442000",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    data = offer.to_dict()
    assert data["price_sgd"] == "4.50"
    assert isinstance(data["price_sgd"], str)
    assert data["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert data["finish"] == "nonfoil"
    assert data["variant_id"] == "41984399442000"
