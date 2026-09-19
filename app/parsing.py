from __future__ import annotations

import csv
import io
import re
from enum import Enum

from app.adapters.base import CardRequest

_FOIL_TRUE = {"yes", "true", "foil", "etched", "1"}
_FOIL_FALSE = {"no", "false", "normal", "nonfoil", "non-foil", "0", ""}
_PLAIN_RE = re.compile(r"^(?P<qty>\d+)\s*[xX]?\s+(?P<name>.+?)\s*$")


class ParseFormat(str, Enum):
    MOXFIELD = "moxfield"
    MANABOX = "manabox"
    PLAIN = "plain"
    UNKNOWN = "unknown"


def _header(text: str) -> list[str]:
    first = text.splitlines()[0] if text.strip() else ""
    return next(csv.reader([first]))


def detect_format(text: str) -> ParseFormat:
    headers = {h.strip().lower() for h in _header(text)}
    if "collector number" in headers and "edition" in headers:
        return ParseFormat.MOXFIELD
    if "set code" in headers and "scryfall id" in headers:
        return ParseFormat.MANABOX
    if "name" in headers:
        return ParseFormat.MANABOX if "set code" in headers else ParseFormat.MOXFIELD
    return ParseFormat.PLAIN


def _foil(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in _FOIL_TRUE:
        return True
    if v in _FOIL_FALSE:
        return False
    return None


def _parse_quantity(value: str | None, default: int = 1) -> int:
    if value is None:
        return default
    v = value.strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _merge(cards: list[CardRequest]) -> list[CardRequest]:
    order: list[str] = []
    totals: dict[str, CardRequest] = {}
    for c in cards:
        if c.key not in totals:
            totals[c.key] = c
            order.append(c.key)
        else:
            prev = totals[c.key]
            totals[c.key] = CardRequest(
                name=prev.name, set_code=prev.set_code,
                collector_number=prev.collector_number,
                quantity=prev.quantity + c.quantity,
                condition=prev.condition, foil=prev.foil,
            )
    return [totals[k] for k in order]


def _parse_csv(text: str, fmt: ParseFormat) -> list[CardRequest]:
    reader = csv.DictReader(io.StringIO(text))
    cards: list[CardRequest] = []
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if fmt == ParseFormat.MOXFIELD:
            name = row.get("Name", "")
            set_code = row.get("Edition") or None
            collector = row.get("Collector Number") or None
            qty = _parse_quantity(row.get("Count"))
            foil = _foil(row.get("Foil"))
            condition = row.get("Condition") or None
        else:
            name = row.get("Name", "")
            set_code = row.get("Set code") or None
            collector = row.get("Collector number") or None
            qty = _parse_quantity(row.get("Quantity"))
            foil = _foil(row.get("Foil"))
            condition = row.get("Condition") or None
        if not name:
            continue
        cards.append(CardRequest(
            name=name, set_code=set_code, collector_number=collector,
            quantity=qty, condition=condition, foil=foil,
        ))
    return cards


def _parse_plain(text: str) -> list[CardRequest]:
    cards: list[CardRequest] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _PLAIN_RE.match(line)
        if m:
            name = m.group("name").strip()
            qty = int(m.group("qty"))
        else:
            name = line
            qty = 1
        cards.append(CardRequest(name=name, quantity=qty))
    return cards


def parse_cards(text: str) -> list[CardRequest]:
    fmt = detect_format(text)
    if fmt == ParseFormat.PLAIN:
        return _merge(_parse_plain(text))
    if fmt in (ParseFormat.MOXFIELD, ParseFormat.MANABOX):
        return _merge(_parse_csv(text, fmt))
    return []
