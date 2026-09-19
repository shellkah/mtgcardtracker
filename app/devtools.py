from __future__ import annotations

import json
from pathlib import Path

from app.http import HttpError


def detect_platform(base_url: str, http) -> str:
    url = base_url.rstrip("/") + "/products.json?limit=1"
    try:
        payload = http.get_json(url)
    except HttpError:
        return "custom"
    if isinstance(payload, dict) and "products" in payload:
        return "shopify"
    if isinstance(payload, list):
        return "other"
    return "unknown"


def dump_for_inspection(url: str, http, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        body = http.get_text(url)
        suffix = "html"
    except HttpError as exc:
        body = f"<!-- error: {exc} -->"
        suffix = "txt"
    path = out_dir / f"dump.{suffix}"
    path.write_text(body)
    return path
