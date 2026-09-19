from __future__ import annotations

import re
from decimal import Decimal

from app.adapters.base import Finish

_NAME_SUFFIX_RE = re.compile(r"\s*[\(\[].*$")
_NUMBER_RE = re.compile(r"\((\d+[a-zA-Z]?)\)")
_SET_RE = re.compile(r"\[([^\]]+)\]")

_CONDITIONS = [
    ("near mint", "NM"), ("nm", "NM"),
    ("lightly played", "LP"), ("light play", "LP"), ("lp", "LP"),
    ("moderately played", "MP"), ("mod play", "MP"), ("mp", "MP"),
    ("heavily played", "HP"), ("heavy play", "HP"), ("hp", "HP"),
    ("damaged", "DMG"), ("dmg", "DMG"),
]


def normalize_name(text: str) -> str:
    base = _NAME_SUFFIX_RE.sub("", text)
    return " ".join(base.strip().lower().split())


def parse_condition(text: str) -> str | None:
    lowered = text.lower()
    for token, code in _CONDITIONS:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return code
    return None


def parse_finish(text: str) -> Finish:
    lowered = text.lower()
    if "surge" in lowered:
        return "surge"
    if "etched" in lowered:
        return "etched"
    if "non-foil" in lowered or "nonfoil" in lowered or "non foil" in lowered:
        return "nonfoil"
    if "foil" in lowered:
        return "foil"
    return "unknown"


def parse_printing_hint(text: str) -> str | None:
    number = _NUMBER_RE.search(text)
    set_match = _SET_RE.search(text)
    if number and set_match:
        return f"{set_match.group(1).strip().lower()}:{number.group(1).strip().lower()}"
    return None


def parse_price(value: str | int | float) -> Decimal:
    return Decimal(str(value))
