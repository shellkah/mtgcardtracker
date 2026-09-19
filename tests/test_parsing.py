from pathlib import Path

from app.parsing import ParseFormat, detect_format, parse_cards

FIXTURES = Path(__file__).parent / "fixtures"


def test_detect_moxfield():
    text = (FIXTURES / "moxfield_sample.csv").read_text()
    assert detect_format(text) == ParseFormat.MOXFIELD


def test_detect_manabox():
    text = (FIXTURES / "manabox_sample.csv").read_text()
    assert detect_format(text) == ParseFormat.MANABOX


def test_detect_plain():
    assert detect_format("1 Sol Ring\n4x Lightning Bolt") == ParseFormat.PLAIN


def test_parse_moxfield_maps_columns():
    cards = parse_cards((FIXTURES / "moxfield_sample.csv").read_text())
    assert cards[0].name == "Sol Ring"
    assert cards[0].set_code == "LTC"
    assert cards[0].collector_number == "409"
    assert cards[0].quantity == 1
    assert cards[0].foil is False
    assert cards[1].foil is True
    assert cards[1].quantity == 2


def test_parse_manabox_maps_columns():
    cards = parse_cards((FIXTURES / "manabox_sample.csv").read_text())
    assert cards[0].set_code == "LTC"
    assert cards[0].foil is False
    assert cards[1].foil is True
    assert cards[1].quantity == 2


def test_parse_plain_sums_duplicates():
    cards = parse_cards("1 Sol Ring\n2 Sol Ring\n4x Lightning Bolt")
    assert len(cards) == 2
    assert cards[0].name == "Sol Ring"
    assert cards[0].quantity == 3
    assert cards[1].name == "Lightning Bolt"
    assert cards[1].quantity == 4


def test_parse_moxfield_non_numeric_quantity_defaults_to_one():
    text = (
        "Count,Name,Edition,Condition,Language,Foil,Collector Number\n"
        "abc,Sol Ring,LTC,Near Mint,English,No,409\n"
    )
    cards = parse_cards(text)
    assert len(cards) == 1
    assert cards[0].name == "Sol Ring"
    assert cards[0].quantity == 1


def test_parse_moxfield_merges_duplicate_rows():
    text = (
        "Count,Name,Edition,Condition,Language,Foil,Collector Number\n"
        "1,Sol Ring,LTC,Near Mint,English,No,409\n"
        "2,Sol Ring,LTC,Near Mint,English,No,409\n"
    )
    cards = parse_cards(text)
    assert len(cards) == 1
    assert cards[0].name == "Sol Ring"
    assert cards[0].set_code == "LTC"
    assert cards[0].collector_number == "409"
    assert cards[0].quantity == 3
