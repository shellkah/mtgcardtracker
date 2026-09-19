from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator

from app.adapters.base import AdapterError, CardRequest, Offer
from app.matching import match


def _card_dict(card: CardRequest) -> dict:
    return {
        "key": card.key,
        "name": card.name,
        "set_code": card.set_code,
        "collector_number": card.collector_number,
        "quantity": card.quantity,
    }


def _offer_from_dict(data: dict) -> Offer:
    kwargs = dict(
        store_id=data["store_id"],
        store_name=data["store_name"],
        title=data["title"],
        price_sgd=Decimal(data["price_sgd"]),
        currency=data["currency"],
        condition=data["condition"],
        finish=data["finish"],
        in_stock=data["in_stock"],
        url=data["url"],
        printing_hint=data.get("printing_hint"),
        image_url=data.get("image_url"),
    )
    fetched_at = data.get("fetched_at")
    if fetched_at is not None:
        kwargs["fetched_at"] = datetime.fromisoformat(fetched_at)
    return Offer(**kwargs)


async def run_search(
    cards,
    adapters,
    db,
    cache_ttl_seconds: int = 21600,
    force_refresh: bool = False,
) -> AsyncIterator[dict]:
    cards = list(cards)
    adapters = list(adapters)
    total = len(cards) * len(adapters)
    yield {"type": "started", "total": total, "card_count": len(cards)}

    summary = {"cards": len(cards), "pairs": total, "offers": 0, "errors": 0}
    max_workers = sum(max(1, getattr(a, "concurrency", 2)) for a in adapters) or 2
    semaphore = asyncio.Semaphore(max_workers)

    async def fetch(adapter, card):
        async with semaphore:
            cache_key = card.key
            if not force_refresh:
                try:
                    cached = await asyncio.to_thread(
                        db.get_cached, adapter.store_id, cache_key, cache_ttl_seconds
                    )
                    offers = (
                        [_offer_from_dict(d) for d in cached.get("offers", [])]
                        if cached is not None
                        else None
                    )
                except Exception:  # cache is best-effort: treat as a miss
                    offers = None
                if offers is not None:
                    return card, adapter, offers, True, None
            try:
                offers = await asyncio.to_thread(adapter.search, card)
            except AdapterError as exc:
                return card, adapter, [], False, str(exc)
            except Exception as exc:  # defensive: never abort the run
                return card, adapter, [], False, f"unexpected: {exc}"
            try:
                await asyncio.to_thread(
                    db.set_cached, adapter.store_id, cache_key,
                    {"offers": [o.to_dict() for o in offers]},
                )
            except Exception:  # cache is best-effort: still return fetched offers
                pass
            return card, adapter, offers, False, None

    tasks = [asyncio.create_task(fetch(a, c)) for c in cards for a in adapters]
    for coro in asyncio.as_completed(tasks):
        card, adapter, offers, cached, error = await coro
        if error is not None:
            summary["errors"] += 1
            yield {"type": "error", "card": _card_dict(card), "store_id": adapter.store_id,
                   "message": error}
            continue
        matched = match(card, offers)
        summary["offers"] += len(matched)
        yield {
            "type": "result",
            "card": _card_dict(card),
            "store_id": adapter.store_id,
            "store_name": adapter.store_name,
            "cached": cached,
            "offers": [m.to_dict() for m in matched],
            "error": None,
        }

    yield {"type": "done", "summary": summary}
