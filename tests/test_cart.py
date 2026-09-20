from app.cart import (
    duplicate_card_keys,
    group_by_store,
    identity_key,
    line_from_offer,
    totals,
)


CARD = {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
        "collector_number": "409", "quantity": 2}


def _offer(**over):
    offer = {
        "store_id": "greyogre", "store_name": "Grey Ogre Games",
        "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
        "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
        "finish": "nonfoil", "in_stock": True, "printing_hint": "ltc:409",
        "image_url": None, "fetched_at": "2026-09-20T00:00:00+00:00",
    }
    offer.update(over)
    return offer


def _item(**over):
    item = line_from_offer(CARD, _offer())
    item["id"] = over.pop("id", 1)
    item["card_key"] = over.pop("card_key", CARD["key"])
    item["card_name"] = over.pop("card_name", CARD["name"])
    item["status"] = over.pop("status", "ok")
    item["in_stock"] = over.pop("in_stock", 1)
    item["price_sgd"] = over.pop("price_sgd", "4.50")
    item["quantity"] = over.pop("quantity", 2)
    item["store_id"] = over.pop("store_id", "greyogre")
    item["store_name"] = over.pop("store_name", "Grey Ogre Games")
    item.update(over)
    return item


def test_identity_key_ignores_price_and_stock():
    a = _offer(price_sgd="4.50", in_stock=True)
    b = _offer(price_sgd="9.99", in_stock=False)
    assert identity_key(a) == identity_key(b)


def test_identity_key_changes_with_condition():
    assert identity_key(_offer(condition="NM")) != identity_key(_offer(condition="LP"))


def test_line_from_offer_copies_card_and_offer_fields():
    row = line_from_offer(CARD, _offer())
    assert row["identity_key"] == identity_key(_offer())
    assert row["card_key"] == "ltc:409"
    assert row["card_name"] == "Sol Ring"
    assert row["quantity"] == 2
    assert row["price_sgd"] == "4.50"
    assert row["in_stock"] == 1
    assert row["status"] == "ok"
    assert row["variant_id"] is None
    assert "added_at" not in row


def test_line_from_offer_defaults_quantity_to_at_least_one():
    row = line_from_offer({**CARD, "quantity": 0}, _offer())
    assert row["quantity"] == 1


def test_line_from_offer_copies_variant_id():
    row = line_from_offer(CARD, _offer(variant_id="41984399442000"))
    assert row["variant_id"] == "41984399442000"


def test_line_from_offer_variant_id_defaults_to_none():
    assert line_from_offer(CARD, _offer())["variant_id"] is None


def test_group_by_store_groups_and_subtotals():
    a = _item(id=1, store_id="greyogre", price_sgd="4.50", quantity=2)
    b = _item(id=2, store_id="hideout", price_sgd="3.00", quantity=1)
    groups = group_by_store([a, b])
    assert [g["store_id"] for g in groups] == ["greyogre", "hideout"]
    assert groups[0]["subtotal_sgd"] == "9.00"
    assert groups[1]["subtotal_sgd"] == "3.00"


def test_group_by_store_excludes_unavailable_from_subtotal():
    ok = _item(id=1, price_sgd="4.50", quantity=2)
    gone = _item(id=2, price_sgd="100.00", status="unavailable")
    groups = group_by_store([ok, gone])
    assert groups[0]["subtotal_sgd"] == "9.00"
    assert len(groups[0]["items"]) == 2


def test_duplicate_card_keys_flags_cards_with_two_lines():
    a = _item(id=1, card_key="ltc:409")
    b = _item(id=2, card_key="ltc:409", store_id="hideout")
    c = _item(id=3, card_key="war:123")
    assert duplicate_card_keys([a, b, c]) == {"ltc:409"}


def test_totals_sums_countable_lines_and_counts_excluded():
    ok = _item(id=1, price_sgd="4.50", quantity=2)
    oos = _item(id=2, price_sgd="1.00", quantity=1, in_stock=0)
    gone = _item(id=3, price_sgd="2.00", quantity=1, status="unavailable")
    result = totals([ok, oos, gone])
    assert result["total_sgd"] == "9.00"
    assert result["excluded"] == 2
