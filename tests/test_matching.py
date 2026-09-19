from decimal import Decimal

from app.adapters.base import CardRequest, Offer
from app.matching import MatchConfidence, match


def _offer(price, hint=None, title="Sol Ring"):
    return Offer(
        store_id="s", store_name="S", title=title,
        price_sgd=Decimal(str(price)), currency="SGD",
        condition="NM", finish="nonfoil", in_stock=True,
        url="https://x/p", printing_hint=hint,
    )


def test_exact_printing_matches_and_drops_other_printings():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    offers = [_offer("4.50", "ltc:409"), _offer("5.00", "ltc:410")]
    results = match(card, offers)
    assert len(results) == 1
    assert results[0].confidence == MatchConfidence.EXACT


def test_exact_printing_keeps_hintless_offer_as_name_only():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    results = match(card, [_offer("4.50", None)])
    assert results[0].confidence == MatchConfidence.NAME_ONLY


def test_name_only_card_single_printing_is_name_only():
    card = CardRequest(name="Sol Ring")
    results = match(card, [_offer("4.50", "ltc:409"), _offer("4.00", "ltc:409")])
    assert all(r.confidence == MatchConfidence.NAME_ONLY for r in results)


def test_name_only_card_multiple_printings_is_ambiguous():
    card = CardRequest(name="Sol Ring")
    results = match(card, [_offer("4.50", "ltc:409"), _offer("5.00", "ltc:410")])
    assert all(r.confidence == MatchConfidence.AMBIGUOUS for r in results)


def test_match_result_to_dict():
    card = CardRequest(name="Sol Ring")
    data = match(card, [_offer("4.50", "ltc:409")])[0].to_dict()
    assert data["confidence"] == "name_only"
    assert data["offer"]["price_sgd"] == "4.50"
    assert data["card"]["name"] == "Sol Ring"
