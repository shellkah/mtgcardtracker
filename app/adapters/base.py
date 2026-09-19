from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

Finish = Literal["nonfoil", "foil", "etched", "surge", "unknown"]


class AdapterError(Exception):
    """Raised by an adapter when a store lookup cannot be completed."""


@dataclass(frozen=True)
class CardRequest:
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    quantity: int = 1
    condition: str | None = None
    foil: bool | None = None

    @property
    def key(self) -> str:
        if self.set_code and self.collector_number:
            return f"{self.set_code.strip().lower()}:{self.collector_number.strip().lower()}"
        return " ".join(self.name.strip().lower().split())


@dataclass
class Offer:
    store_id: str
    store_name: str
    title: str
    price_sgd: Decimal
    currency: str
    condition: str | None
    finish: Finish
    in_stock: bool
    url: str
    printing_hint: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "store_id": self.store_id,
            "store_name": self.store_name,
            "title": self.title,
            "price_sgd": str(self.price_sgd),
            "currency": self.currency,
            "condition": self.condition,
            "finish": self.finish,
            "in_stock": self.in_stock,
            "url": self.url,
            "printing_hint": self.printing_hint,
            "fetched_at": self.fetched_at.isoformat(),
        }


@runtime_checkable
class Adapter(Protocol):
    store_id: str
    store_name: str
    zone: str
    needs_browser: bool

    def search(self, card: CardRequest) -> list[Offer]:
        ...
