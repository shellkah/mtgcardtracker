from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapters.base import CardRequest
from app.adapters.registry import build_adapters, load_config
from app.cart import line_from_offer
from app.db import Database
from app.http import HttpxClient
from app.parsing import detect_format, parse_cards
from app.runs import RunRegistry

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _make_http() -> HttpxClient:
    return HttpxClient("mtgcardtracker/0.1 (+local tool)")


class RunRequest(BaseModel):
    text: str
    store_ids: list[str] = []
    force_refresh: bool = False


class ListRequest(BaseModel):
    name: str
    text: str


class CartCard(BaseModel):
    key: str
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    quantity: int = 1


class CartOffer(BaseModel):
    store_id: str
    store_name: str
    title: str
    url: str
    variant_id: str | None = None
    price_sgd: str
    currency: str = "SGD"
    condition: str | None = None
    finish: str = "unknown"
    in_stock: bool = True
    printing_hint: str | None = None
    image_url: str | None = None
    fetched_at: str | None = None


class AddCartRequest(BaseModel):
    card: CartCard
    offer: CartOffer


class QuantityRequest(BaseModel):
    quantity: int


class RefreshItemRequest(BaseModel):
    status: str
    price_sgd: str | None = None
    condition: str | None = None
    finish: str | None = None
    variant_id: str | None = None
    in_stock: bool | None = None
    offer_fetched_at: str | None = None
    last_checked_at: float | None = None


def create_app(config_path: str = "config/stores.yaml", db_path: str = "mtgcardtracker.db") -> FastAPI:
    app = FastAPI(title="MTG Card Tracker")
    configs = load_config(config_path)
    db = Database(db_path)
    db.init_schema()
    registry = RunRegistry()

    @app.get("/api/stores")
    def stores():
        zones = {}
        for cfg in configs:
            zones.setdefault(cfg.zone, {"id": cfg.zone, "name": cfg.zone.title()})
        return {
            "zones": list(zones.values()),
            "stores": [
                {"id": c.id, "name": c.name, "zone": c.zone, "enabled": c.enabled,
                 "needs_browser": c.needs_browser}
                for c in configs
            ],
        }

    @app.post("/api/runs")
    async def create_run(request: RunRequest):
        cards = parse_cards(request.text)
        if not cards:
            raise HTTPException(status_code=400, detail="No cards parsed from input")
        selected = [c for c in configs if c.id in request.store_ids] if request.store_ids else configs
        adapters = build_adapters(selected, _make_http())
        run = registry.create(cards, adapters, db, force_refresh=request.force_refresh)
        return {"run_id": run.id, "card_count": len(cards)}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str):
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Unknown run")

        async def event_source():
            async for event in run.subscribe():
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

        return StreamingResponse(event_source(), media_type="text/event-stream")

    @app.get("/api/lists")
    def list_saved():
        return db.list_saved()

    @app.post("/api/lists")
    def create_list(request: ListRequest):
        fmt = detect_format(request.text).value
        list_id = db.save_list(request.name, request.text, fmt)
        return {"id": list_id}

    @app.get("/api/lists/{list_id}")
    def get_list(list_id: int):
        saved = db.get_saved(list_id)
        if saved is None:
            raise HTTPException(status_code=404, detail="Not found")
        return saved

    @app.delete("/api/lists/{list_id}")
    def delete_list(list_id: int):
        db.delete_saved(list_id)
        return {"ok": True}

    @app.get("/api/cart")
    def get_cart():
        items = db.list_cart()
        return {"items": items, "count": len(items)}

    @app.post("/api/cart")
    def add_to_cart(request: AddCartRequest):
        row = line_from_offer(request.card.model_dump(), request.offer.model_dump())
        item_id, created = db.add_cart_item(row)
        return {"id": item_id, "created": created}

    @app.patch("/api/cart/{item_id}")
    def set_cart_quantity(item_id: int, request: QuantityRequest):
        item = db.update_cart_quantity(item_id, request.quantity)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.delete("/api/cart/{item_id}")
    def remove_from_cart(item_id: int):
        db.remove_cart_item(item_id)
        return {"ok": True}

    @app.delete("/api/cart")
    def clear_cart():
        db.clear_cart()
        return {"ok": True}

    @app.patch("/api/cart/{item_id}/refresh")
    def refresh_cart_item(item_id: int, request: RefreshItemRequest):
        fields: dict = {
            "status": request.status,
            "last_checked_at": request.last_checked_at,
        }
        if request.status == "ok":
            fields.update(
                price_sgd=request.price_sgd,
                condition=request.condition,
                finish=request.finish,
                in_stock=1 if request.in_stock else 0,
                offer_fetched_at=request.offer_fetched_at,
            )
            if request.variant_id is not None:
                fields["variant_id"] = request.variant_id
        item = db.update_cart_item(item_id, **fields)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.post("/api/cart/refresh")
    async def refresh_cart():
        items = db.list_cart()
        if not items:
            raise HTTPException(status_code=400, detail="Cart is empty")

        cards: dict[str, CardRequest] = {}
        for item in items:
            if item["card_key"] in cards:
                continue
            cards[item["card_key"]] = CardRequest(
                name=item["card_name"],
                set_code=item["set_code"],
                collector_number=item["collector_number"],
                quantity=item["quantity"],
            )

        wanted = {item["store_id"] for item in items}
        selected = [c for c in configs if c.id in wanted and c.enabled]
        skipped = sorted(wanted - {c.id for c in selected})
        if not selected:
            raise HTTPException(status_code=409, detail="No cart stores are enabled")

        adapters = build_adapters(selected, _make_http())
        run = registry.create(list(cards.values()), adapters, db, force_refresh=True)
        return {
            "run_id": run.id,
            "card_count": len(cards),
            "store_ids": [c.id for c in selected],
            "skipped_store_ids": skipped,
        }

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
