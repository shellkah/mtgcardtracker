from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

IDENTITY_FIELDS = ("store_id", "title", "condition", "finish", "url")


def identity_key(offer: dict) -> str:
    """Stable identity for an offer, mirroring the front end's offerIndexKey."""
    return "|".join(str(offer.get(field) or "") for field in IDENTITY_FIELDS)


def line_from_offer(card: dict, offer: dict) -> dict:
    """Normalise an API add payload into a cart_items storage row.

    `added_at` is intentionally omitted; the database stamps it on insert.
    """
    return {
        "identity_key": identity_key(offer),
        "card_key": card["key"],
        "card_name": card["name"],
        "set_code": card.get("set_code"),
        "collector_number": card.get("collector_number"),
        "store_id": offer["store_id"],
        "store_name": offer["store_name"],
        "title": offer["title"],
        "url": offer["url"],
        "printing_hint": offer.get("printing_hint"),
        "image_url": offer.get("image_url"),
        "condition": offer.get("condition"),
        "finish": offer.get("finish") or "unknown",
        "price_sgd": str(offer["price_sgd"]),
        "currency": offer.get("currency") or "SGD",
        "in_stock": 1 if offer.get("in_stock") else 0,
        "quantity": max(1, int(card.get("quantity") or 1)),
        "status": "ok",
        "offer_fetched_at": offer.get("fetched_at")
        or datetime.now(timezone.utc).isoformat(),
    }


def _line_total(item: dict) -> Decimal:
    return Decimal(str(item["price_sgd"])) * int(item["quantity"])


def _countable(item: dict) -> bool:
    return item["status"] == "ok" and bool(item["in_stock"])


def group_by_store(items: list[dict]) -> list[dict]:
    """Group cart lines into store sections, preserving first-seen order."""
    sections: dict[str, dict] = {}
    for item in items:
        section = sections.setdefault(
            item["store_id"],
            {
                "store_id": item["store_id"],
                "store_name": item["store_name"],
                "items": [],
                "subtotal_sgd": Decimal("0"),
            },
        )
        section["items"].append(item)
        if _countable(item):
            section["subtotal_sgd"] += _line_total(item)
    for section in sections.values():
        section["subtotal_sgd"] = str(section["subtotal_sgd"])
    return list(sections.values())


def duplicate_card_keys(items: list[dict]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        key = item["card_key"]
        if key in seen:
            dupes.add(key)
        seen.add(key)
    return dupes


def totals(items: list[dict]) -> dict:
    total = Decimal("0")
    excluded = 0
    for item in items:
        if _countable(item):
            total += _line_total(item)
        else:
            excluded += 1
    return {"total_sgd": str(total), "excluded": excluded}
