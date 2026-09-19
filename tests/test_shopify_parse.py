from decimal import Decimal

from app.adapters.shopify_parse import (
    normalize_name,
    parse_condition,
    parse_finish,
    parse_price,
    parse_printing_hint,
)


def test_normalize_name_strips_printing_suffixes():
    assert normalize_name("Sol Ring (409) (Dwarven) [LTC Commander]") == "sol ring"


def test_parse_condition_variants():
    assert parse_condition("Near Mint") == "NM"
    assert parse_condition("Lightly Played Foil") == "LP"
    assert parse_condition("MP") == "MP"
    assert parse_condition("Damaged") == "DMG"
    assert parse_condition("no condition here") is None


def test_parse_finish_priority():
    assert parse_finish("Surge Foil") == "surge"
    assert parse_finish("Etched Foil") == "etched"
    assert parse_finish("Foil") == "foil"
    assert parse_finish("Non-Foil") == "nonfoil"
    assert parse_finish("Survivor: Non Foil") == "nonfoil"
    assert parse_finish("") == "unknown"


def test_parse_printing_hint():
    assert parse_printing_hint("Sol Ring (409) [LTC]") == "ltc:409"
    assert parse_printing_hint("Sol Ring [LTC]") is None


def test_parse_price():
    assert parse_price("4.50") == Decimal("4.50")
    assert parse_price(3) == Decimal("3")
