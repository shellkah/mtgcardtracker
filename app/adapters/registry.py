from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.adapters.base import Adapter


@dataclass(frozen=True)
class StoreConfig:
    id: str
    name: str
    zone: str
    adapter_path: str
    base_url: str
    enabled: bool = True
    requests_per_second: float = 1.0
    concurrency: int = 2
    needs_browser: bool = False
    options: dict = field(default_factory=dict)


def load_config(path: str | Path) -> list[StoreConfig]:
    raw = yaml.safe_load(Path(path).read_text())
    stores = []
    for item in raw.get("stores", []):
        stores.append(StoreConfig(
            id=item["id"],
            name=item["name"],
            zone=item["zone"],
            adapter_path=item["adapter"],
            base_url=item["base_url"].rstrip("/"),
            enabled=item.get("enabled", True),
            requests_per_second=item.get("requests_per_second", 1.0),
            concurrency=item.get("concurrency", 2),
            needs_browser=item.get("needs_browser", False),
            options=item.get("options", {}),
        ))
    return stores


def _import_class(path: str):
    module_name, _, class_name = path.partition(":")
    if not module_name or not class_name:
        raise ValueError(f"adapter path must be 'module:Class', got {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def build_adapters(configs, http, browser=None) -> list[Adapter]:
    adapters = []
    for cfg in configs:
        if not cfg.enabled:
            continue
        cls = _import_class(cfg.adapter_path)
        if cfg.needs_browser:
            adapters.append(cls(cfg, http, browser))
        else:
            adapters.append(cls(cfg, http))
    return adapters
