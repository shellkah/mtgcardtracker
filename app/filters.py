from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.base import Offer


@dataclass
class OfferFilters:
    conditions: list[str] = field(default_factory=list)
    finishes: list[str] = field(default_factory=list)
    in_stock_only: bool = False


def filter_offers(offers: list[Offer], filters: OfferFilters) -> list[Offer]:
    result = []
    for offer in offers:
        if filters.in_stock_only and not offer.in_stock:
            continue
        if filters.conditions and offer.condition not in filters.conditions:
            continue
        if filters.finishes and offer.finish not in filters.finishes:
            continue
        result.append(offer)
    return result
