from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapters.registry import build_adapters, load_config
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
    def create_run(request: RunRequest):
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

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
