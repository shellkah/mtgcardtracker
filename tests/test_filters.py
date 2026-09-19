from decimal import Decimal

from app.adapters.base import Offer
from app.filters import OfferFilters, filter_offers


def _offer(condition, finish, in_stock=True):
    return Offer(
        store_id="s", store_name="S", title="t",
        price_sgd=Decimal("1.00"), currency="SGD", condition=condition,
        finish=finish, in_stock=in_stock, url="https://x",
    )


def test_filter_in_stock_only():
    result = filter_offers([_offer("NM", "nonfoil", False)], OfferFilters(in_stock_only=True))
    assert result == []


def test_filter_conditions():
    offers = [_offer("NM", "nonfoil"), _offer("LP", "nonfoil")]
    result = filter_offers(offers, OfferFilters(conditions=["NM"]))
    assert [o.condition for o in result] == ["NM"]


def test_filter_finish():
    offers = [_offer("NM", "nonfoil"), _offer("NM", "foil")]
    result = filter_offers(offers, OfferFilters(finishes=["foil"]))
    assert [o.finish for o in result] == ["foil"]


def test_filters_are_and_combined():
    offers = [_offer("NM", "foil"), _offer("LP", "foil"), _offer("NM", "nonfoil")]
    result = filter_offers(offers, OfferFilters(conditions=["NM"], finishes=["foil"]))
    assert len(result) == 1
    assert result[0].condition == "NM" and result[0].finish == "foil"
