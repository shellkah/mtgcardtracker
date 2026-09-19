from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.adapters.base import CardRequest, Offer


class MatchConfidence(str, Enum):
    EXACT = "exact"
    NAME_ONLY = "name_only"
    AMBIGUOUS = "ambiguous"


@dataclass
class MatchResult:
    card: CardRequest
    offer: Offer
    confidence: MatchConfidence

    def to_dict(self) -> dict:
        return {
            "card": {
                "key": self.card.key,
                "name": self.card.name,
                "set_code": self.card.set_code,
                "collector_number": self.card.collector_number,
                "quantity": self.card.quantity,
            },
            "offer": self.offer.to_dict(),
            "confidence": self.confidence.value,
        }


def _card_dict(card: CardRequest) -> dict:
    return {
        "key": card.key,
        "name": card.name,
        "set_code": card.set_code,
        "collector_number": card.collector_number,
        "quantity": card.quantity,
    }


def match(card: CardRequest, offers: list[Offer]) -> list[MatchResult]:
    if card.set_code and card.collector_number:
        wanted = f"{card.set_code.strip().lower()}:{card.collector_number.strip().lower()}"
        results: list[MatchResult] = []
        for offer in offers:
            if offer.printing_hint == wanted:
                results.append(MatchResult(card, offer, MatchConfidence.EXACT))
            elif offer.printing_hint is None:
                results.append(MatchResult(card, offer, MatchConfidence.NAME_ONLY))
        return results

    hints = {offer.printing_hint for offer in offers if offer.printing_hint}
    confidence = MatchConfidence.AMBIGUOUS if len(hints) > 1 else MatchConfidence.NAME_ONLY
    return [MatchResult(card, offer, confidence) for offer in offers]
