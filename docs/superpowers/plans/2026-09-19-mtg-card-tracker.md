# MTG Card Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local web app that parses a Moxfield/ManaBox card list, scrapes configured Singapore card stores via per-store adapters, and streams a per-card offer comparison.

**Architecture:** A single FastAPI process serves a no-build vanilla-JS frontend and an SSE search stream. Core logic (parsing, matching, DB cache) is decoupled from store adapters. Adapters are synchronous Python classes registered from `config/stores.yaml`; most extend a shared `ShopifyAdapter`, custom stores implement the same protocol directly. Adapters are run in a bounded thread pool by an async orchestrator.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, PyYAML, sqlite3 (stdlib), pytest. Playwright is optional (opt-in per adapter).

## Global Constraints

- Python >= 3.11.
- v1 assumes all prices are SGD; `Offer.currency` is always `"SGD"`.
- Frontend has **no build step** (plain HTML/CSS/JS using `EventSource`).
- Adapters are synchronous; they MUST NOT perform matching or caching.
- Matching logic lives only in `app/matching.py`.
- Adapter failures are isolated per `(card, store)` and never abort a run.
- Default cache TTL is 21600 seconds (6h).
- Every adapter must have an offline contract test backed by a fixture.
- No proxy rotation or anti-bot evasion. Send an identifying `User-Agent`.

---

### Task 1: Project skeleton and core models

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/adapters/__init__.py`
- Create: `app/adapters/base.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Finish = Literal["nonfoil","foil","etched","surge","unknown"]`
  - `CardRequest(name, set_code=None, collector_number=None, quantity=1, condition=None, foil=None)` with property `key -> str`
  - `Offer(store_id, store_name, title, price_sgd, currency, condition, finish, in_stock, url, printing_hint=None, fetched_at=...)` with method `to_dict() -> dict`
  - `AdapterError(Exception)`
  - `Adapter` Protocol with attributes `store_id`, `store_name`, `zone`, `needs_browser` and method `search(card: CardRequest) -> list[Offer]`

- [ ] **Step 1: Create `requirements.txt` and `pytest.ini`**

`requirements.txt`:

```text
fastapi==0.115.*
uvicorn[standard]==0.30.*
httpx==0.27.*
PyYAML==6.0.*
pytest==8.*
```

`pytest.ini`:

```ini
[pytest]
markers =
    live: hits real store networks; opt-in only via `pytest -m live`
```

- [ ] **Step 2: Write the failing test** — `tests/test_models.py`

```python
from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.base import CardRequest, Offer


def test_card_key_uses_printing_when_present():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    assert card.key == "ltc:409"


def test_card_key_falls_back_to_normalized_name():
    card = CardRequest(name="  Sol Ring  ")
    assert card.key == "sol ring"


def test_offer_to_dict_serializes_decimal_and_datetime():
    offer = Offer(
        store_id="greyogre",
        store_name="Grey Ogre Games",
        title="Sol Ring (409) [LTC]",
        price_sgd=Decimal("4.50"),
        currency="SGD",
        condition="NM",
        finish="nonfoil",
        in_stock=True,
        url="https://example.com/p/sol-ring",
        printing_hint="ltc:409",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    data = offer.to_dict()
    assert data["price_sgd"] == "4.50"
    assert isinstance(data["price_sgd"], str)
    assert data["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert data["finish"] == "nonfoil"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.base'` (create empty `__init__.py` files first so the package imports).

- [ ] **Step 4: Write minimal implementation** — `app/adapters/base.py`

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app tests
git commit -m "feat: project skeleton and core models"
```

---

### Task 2: Store configuration and adapter registry

**Files:**
- Create: `config/stores.yaml`
- Create: `app/adapters/registry.py`
- Create: `tests/test_registry.py`

**Interfaces:**
- Consumes: `Adapter`, `AdapterError` from Task 1.
- Produces:
  - `StoreConfig(id, name, zone, adapter_path, base_url, enabled=True, requests_per_second=1.0, concurrency=2, needs_browser=False, options={})`
  - `load_config(path) -> list[StoreConfig]` (only `enabled` stores are returned by `build_adapters`, but `load_config` returns all)
  - `build_adapters(configs, http, browser=None) -> list[Adapter]`
  - `ZoneConfig` is not needed; zones are derived from `StoreConfig.zone`.

- [ ] **Step 1: Create `config/stores.yaml`**

```yaml
zones:
  - id: singapore
    name: Singapore
stores:
  - id: greyogre
    name: Grey Ogre Games
    zone: singapore
    adapter: app.adapters.greyogre:GreyOgreAdapter
    base_url: https://www.greyogregames.com
    enabled: true
    requests_per_second: 1.0
    concurrency: 2
    needs_browser: false
```

- [ ] **Step 2: Write the failing test** — `tests/test_registry.py`

```python
from pathlib import Path

from app.adapters.registry import StoreConfig, build_adapters, load_config


class FakeHttp:
    def get_json(self, url, params=None):
        return {"resources": {"results": {"products": []}}}

    def get_text(self, url, params=None):
        return ""


def test_load_config_parses_stores():
    configs = load_config(Path("config/stores.yaml"))
    grey = next(c for c in configs if c.id == "greyogre")
    assert grey.zone == "singapore"
    assert grey.requests_per_second == 1.0
    assert grey.needs_browser is False


def test_build_adapters_imports_adapter_class():
    configs = [StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )]
    adapters = build_adapters(configs, FakeHttp())
    assert len(adapters) == 1
    assert adapters[0].store_id == "greyogre"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.registry'`.

- [ ] **Step 4: Write minimal implementation** — `app/adapters/registry.py`

```python
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
```

- [ ] **Step 5: Create `app/adapters/greyogre.py` with a non-functional stub** so the import test passes (filled in Task 8)

```python
from __future__ import annotations

from app.adapters.base import AdapterError, CardRequest, Offer


class GreyOgreAdapter:
    store_id: str
    store_name: str
    zone: str
    needs_browser = False

    def __init__(self, config, http):
        self.store_id = config.id
        self.store_name = config.name
        self.zone = config.zone
        self._config = config
        self._http = http

    def search(self, card: CardRequest) -> list[Offer]:
        raise AdapterError("not implemented")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add config app/adapters/registry.py app/adapters/greyogre.py tests/test_registry.py
git commit -m "feat: store config and adapter registry"
```

---

### Task 3: SQLite cache and saved lists

**Files:**
- Create: `app/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Database(path)` with:
  - `init_schema() -> None`
  - `get_cached(store_id: str, card_key: str, ttl_seconds: int) -> dict | None`
  - `set_cached(store_id: str, card_key: str, payload: dict) -> None`
  - `save_list(name: str, raw_text: str, fmt: str) -> int`
  - `list_saved() -> list[dict]` (newest first; keys `id, name, format, created_at`)
  - `get_saved(list_id: int) -> dict | None` (includes `raw_text`)
  - `delete_saved(list_id: int) -> None`
  - `close() -> None`

- [ ] **Step 1: Write the failing test** — `tests/test_db.py`

```python
from app.db import Database


def test_cache_roundtrip_and_ttl(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.set_cached("greyogre", "ltc:409", {"offers": []})
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=3600) == {"offers": []}
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=-1) is None
    assert db.get_cached("other", "ltc:409", ttl_seconds=3600) is None
    db.close()


def test_saved_lists_crud(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    list_id = db.save_list("My Deck", "1 Sol Ring", "plain")
    saved = db.list_saved()
    assert saved[0]["id"] == list_id
    assert saved[0]["name"] == "My Deck"
    assert db.get_saved(list_id)["raw_text"] == "1 Sol Ring"
    db.delete_saved(list_id)
    assert db.list_saved() == []
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`.

- [ ] **Step 3: Write minimal implementation** — `app/db.py`

```python
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    store_id   TEXT NOT NULL,
    card_key   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (store_id, card_key)
);
CREATE TABLE IF NOT EXISTS saved_lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    raw_text   TEXT NOT NULL,
    format     TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def get_cached(self, store_id: str, card_key: str, ttl_seconds: int) -> dict | None:
        row = self._conn.execute(
            "SELECT payload, fetched_at FROM cache WHERE store_id=? AND card_key=?",
            (store_id, card_key),
        ).fetchone()
        if row is None:
            return None
        if ttl_seconds >= 0 and time.time() - row["fetched_at"] > ttl_seconds:
            return None
        return json.loads(row["payload"])

    def set_cached(self, store_id: str, card_key: str, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO cache (store_id, card_key, payload, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(store_id, card_key) DO UPDATE SET payload=excluded.payload, "
            "fetched_at=excluded.fetched_at",
            (store_id, card_key, json.dumps(payload), time.time()),
        )
        self._conn.commit()

    def save_list(self, name: str, raw_text: str, fmt: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO saved_lists (name, raw_text, format, created_at) VALUES (?,?,?,?)",
            (name, raw_text, fmt, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_saved(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name, format, created_at FROM saved_lists ORDER BY created_at DESC"
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "format": r["format"], "created_at": r["created_at"]}
            for r in rows
        ]

    def get_saved(self, list_id: int) -> dict | None:
        r = self._conn.execute(
            "SELECT id, name, format, raw_text, created_at FROM saved_lists WHERE id=?",
            (list_id,),
        ).fetchone()
        if r is None:
            return None
        return {
            "id": r["id"], "name": r["name"], "format": r["format"],
            "raw_text": r["raw_text"], "created_at": r["created_at"],
        }

    def delete_saved(self, list_id: int) -> None:
        self._conn.execute("DELETE FROM saved_lists WHERE id=?", (list_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: sqlite cache and saved lists"
```

---

### Task 4: Card list parsing

**Files:**
- Create: `app/parsing.py`
- Create: `tests/test_parsing.py`
- Create: `tests/fixtures/moxfield_sample.csv`
- Create: `tests/fixtures/manabox_sample.csv`

**Interfaces:**
- Consumes: `CardRequest` from Task 1.
- Produces:
  - `ParseFormat(str, Enum)` with `MOXFIELD`, `MANABOX`, `PLAIN`, `UNKNOWN`
  - `detect_format(text: str) -> ParseFormat`
  - `parse_cards(text: str) -> list[CardRequest]` (merges duplicate keys, sums quantities, preserves first-seen order)

- [ ] **Step 1: Create fixtures** — `tests/fixtures/moxfield_sample.csv`

```csv
Count,Name,Edition,Condition,Language,Foil,Collector Number
1,Sol Ring,LTC,Near Mint,English,No,409
2,Llanowar Elves,FDN,Near Mint,English,Yes,101
```

`tests/fixtures/manabox_sample.csv`

```csv
Name,Set code,Set name,Collector number,Foil,Quantity,Condition,Language,Scryfall ID
Sol Ring,LTC,Lost Caverns Commander,409,normal,1,near_mint,en,abc
Llanowar Elves,FDN,Foundations,101,foil,2,near_mint,en,def
```

- [ ] **Step 2: Write the failing test** — `tests/test_parsing.py`

```python
from pathlib import Path

from app.parsing import ParseFormat, detect_format, parse_cards

FIXTURES = Path(__file__).parent / "fixtures"


def test_detect_moxfield():
    text = (FIXTURES / "moxfield_sample.csv").read_text()
    assert detect_format(text) == ParseFormat.MOXFIELD


def test_detect_manabox():
    text = (FIXTURES / "manabox_sample.csv").read_text()
    assert detect_format(text) == ParseFormat.MANABOX


def test_detect_plain():
    assert detect_format("1 Sol Ring\n4x Lightning Bolt") == ParseFormat.PLAIN


def test_parse_moxfield_maps_columns():
    cards = parse_cards((FIXTURES / "moxfield_sample.csv").read_text())
    assert cards[0].name == "Sol Ring"
    assert cards[0].set_code == "LTC"
    assert cards[0].collector_number == "409"
    assert cards[0].quantity == 1
    assert cards[0].foil is False
    assert cards[1].foil is True
    assert cards[1].quantity == 2


def test_parse_manabox_maps_columns():
    cards = parse_cards((FIXTURES / "manabox_sample.csv").read_text())
    assert cards[0].set_code == "LTC"
    assert cards[0].foil is False
    assert cards[1].foil is True
    assert cards[1].quantity == 2


def test_parse_plain_sums_duplicates():
    cards = parse_cards("1 Sol Ring\n2 Sol Ring\n4x Lightning Bolt")
    assert len(cards) == 2
    assert cards[0].name == "Sol Ring"
    assert cards[0].quantity == 3
    assert cards[1].name == "Lightning Bolt"
    assert cards[1].quantity == 4
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.parsing'`.

- [ ] **Step 4: Write minimal implementation** — `app/parsing.py`

```python
from __future__ import annotations

import csv
import io
import re
from enum import Enum

from app.adapters.base import CardRequest

_FOIL_TRUE = {"yes", "true", "foil", "etched", "1"}
_FOIL_FALSE = {"no", "false", "normal", "nonfoil", "non-foil", "0", ""}
_PLAIN_RE = re.compile(r"^(?P<qty>\d+)\s*[xX]?\s+(?P<name>.+?)\s*$")


class ParseFormat(str, Enum):
    MOXFIELD = "moxfield"
    MANABOX = "manabox"
    PLAIN = "plain"
    UNKNOWN = "unknown"


def _header(text: str) -> list[str]:
    first = text.splitlines()[0] if text.strip() else ""
    return next(csv.reader([first]))


def detect_format(text: str) -> ParseFormat:
    headers = {h.strip().lower() for h in _header(text)}
    if "collector number" in headers and "edition" in headers:
        return ParseFormat.MOXFIELD
    if "set code" in headers and "scryfall id" in headers:
        return ParseFormat.MANABOX
    if "name" in headers:
        return ParseFormat.MANABOX if "set code" in headers else ParseFormat.MOXFIELD
    return ParseFormat.PLAIN


def _foil(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in _FOIL_TRUE:
        return True
    if v in _FOIL_FALSE:
        return False
    return None


def _merge(cards: list[CardRequest]) -> list[CardRequest]:
    order: list[str] = []
    totals: dict[str, CardRequest] = {}
    for c in cards:
        if c.key not in totals:
            totals[c.key] = c
            order.append(c.key)
        else:
            prev = totals[c.key]
            totals[c.key] = CardRequest(
                name=prev.name, set_code=prev.set_code,
                collector_number=prev.collector_number,
                quantity=prev.quantity + c.quantity,
                condition=prev.condition, foil=prev.foil,
            )
    return [totals[k] for k in order]


def _parse_csv(text: str, fmt: ParseFormat) -> list[CardRequest]:
    reader = csv.DictReader(io.StringIO(text))
    cards: list[CardRequest] = []
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if fmt == ParseFormat.MOXFIELD:
            name = row.get("Name", "")
            set_code = row.get("Edition") or None
            collector = row.get("Collector Number") or None
            qty = int(row.get("Count") or 1)
            foil = _foil(row.get("Foil"))
            condition = row.get("Condition") or None
        else:
            name = row.get("Name", "")
            set_code = row.get("Set code") or None
            collector = row.get("Collector number") or None
            qty = int(row.get("Quantity") or 1)
            foil = _foil(row.get("Foil"))
            condition = row.get("Condition") or None
        if not name:
            continue
        cards.append(CardRequest(
            name=name, set_code=set_code, collector_number=collector,
            quantity=qty, condition=condition, foil=foil,
        ))
    return cards


def _parse_plain(text: str) -> list[CardRequest]:
    cards: list[CardRequest] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _PLAIN_RE.match(line)
        if m:
            name = m.group("name").strip()
            qty = int(m.group("qty"))
        else:
            name = line
            qty = 1
        cards.append(CardRequest(name=name, quantity=qty))
    return cards


def parse_cards(text: str) -> list[CardRequest]:
    fmt = detect_format(text)
    if fmt == ParseFormat.PLAIN:
        return _merge(_parse_plain(text))
    if fmt in (ParseFormat.MOXFIELD, ParseFormat.MANABOX):
        return _merge(_parse_csv(text, fmt))
    return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/parsing.py tests/test_parsing.py tests/fixtures
git commit -m "feat: moxfield/manabox/plain card list parsing"
```

---

### Task 5: HTTP client abstraction

**Files:**
- Create: `app/http.py`
- Create: `tests/test_http.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `HttpError(Exception)`
  - `HttpClient` Protocol: `get_json(url, params=None) -> dict`, `get_text(url, params=None) -> str`
  - `HttpxClient(user_agent: str, timeout: float = 15.0, transport=None)` implementing `HttpClient`; `transport` accepts an `httpx.BaseTransport` for testing.

- [ ] **Step 1: Write the failing test** — `tests/test_http.py`

```python
import httpx
import pytest

from app.http import HttpError, HttpxClient


def _client(handler):
    return HttpxClient("mtgcardtracker/0.1", transport=httpx.MockTransport(handler))


def test_get_json_ok():
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    assert _client(handler).get_json("https://x.test/a") == {"ok": True}


def test_get_json_raises_on_error_status():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(HttpError):
        _client(handler).get_json("https://x.test/a")


def test_get_text_returns_body():
    def handler(request):
        return httpx.Response(200, text="<html>hi</html>")

    assert _client(handler).get_text("https://x.test/a") == "<html>hi</html>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.http'`.

- [ ] **Step 3: Write minimal implementation** — `app/http.py`

```python
from __future__ import annotations

from typing import Protocol

import httpx


class HttpError(Exception):
    pass


class HttpClient(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> dict:
        ...

    def get_text(self, url: str, params: dict | None = None) -> str:
        ...


class HttpxClient:
    def __init__(self, user_agent: str, timeout: float = 15.0, transport=None):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            transport=transport,
        )

    def get_json(self, url: str, params: dict | None = None) -> dict:
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise HttpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            raise HttpError(f"{url} returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} did not return JSON") from exc

    def get_text(self, url: str, params: dict | None = None) -> str:
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise HttpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            raise HttpError(f"{url} returned {response.status_code}")
        return response.text

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_http.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/http.py tests/test_http.py
git commit -m "feat: http client abstraction"
```

---

### Task 6: Shopify parsing helpers

**Files:**
- Create: `app/adapters/shopify_parse.py`
- Create: `tests/test_shopify_parse.py`

**Interfaces:**
- Consumes: `Finish` from Task 1.
- Produces:
  - `normalize_name(text: str) -> str`
  - `parse_condition(text: str) -> str | None` returning one of `NM, LP, MP, HP, DMG`
  - `parse_finish(text: str) -> Finish`
  - `parse_printing_hint(text: str) -> str | None` returning `"set:collector"` lowercased when both are found
  - `parse_price(value: str | int | float) -> Decimal`

- [ ] **Step 1: Write the failing test** — `tests/test_shopify_parse.py`

```python
from decimal import Decimal

from app.adapters.shopify_parse import (
    normalize_name,
    parse_condition,
    parse_finish,
    parse_price,
    parse_printing_hint,
)


def test_normalize_name_strips_printing_suffixes():
    assert normalize_name("Sol Ring (409) (Dwarven) [LTC Commander]") == "sol ring"


def test_parse_condition_variants():
    assert parse_condition("Near Mint") == "NM"
    assert parse_condition("Lightly Played Foil") == "LP"
    assert parse_condition("MP") == "MP"
    assert parse_condition("Damaged") == "DMG"
    assert parse_condition("no condition here") is None


def test_parse_finish_priority():
    assert parse_finish("Surge Foil") == "surge"
    assert parse_finish("Etched Foil") == "etched"
    assert parse_finish("Foil") == "foil"
    assert parse_finish("Non-Foil") == "nonfoil"
    assert parse_finish("") == "unknown"


def test_parse_printing_hint():
    assert parse_printing_hint("Sol Ring (409) [LTC]") == "ltc:409"
    assert parse_printing_hint("Sol Ring [LTC]") is None


def test_parse_price():
    assert parse_price("4.50") == Decimal("4.50")
    assert parse_price(3) == Decimal("3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shopify_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.shopify_parse'`.

- [ ] **Step 3: Write minimal implementation** — `app/adapters/shopify_parse.py`

```python
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
    if "non-foil" in lowered or "nonfoil" in lowered:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shopify_parse.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/adapters/shopify_parse.py tests/test_shopify_parse.py
git commit -m "feat: shopify listing parsing helpers"
```

---

### Task 7: Scryfall set resolver and printing-hint normalization

**Files:**
- Create: `app/sets.py`
- Create: `tests/test_sets.py`
- Modify: `app/adapters/shopify_parse.py`
- Modify: `tests/test_shopify_parse.py`

**Interfaces:**
- Consumes: `HttpClient` (Task 5); `parse_printing_hint` (Task 6).
- Produces:
  - `normalize_set_name(name: str) -> str`
  - `SetResolver(http)` with `code_for(set_name: str) -> str | None` — fetches `https://api.scryfall.com/sets` once per instance, maps a normalized Scryfall set name to its lowercase code, and returns `None` for unknown names or on any HTTP failure.
  - `parse_printing_hint(text: str, set_code_lookup: Callable[[str], str | None] | None = None) -> str | None` — with a lookup, resolves the bracket text to a code (returning `None` when it cannot be resolved); without one, preserves Task 6 behavior (raw lowercased bracket as the set id).

**Why:** real Shopify titles put the full set name in brackets (e.g. `[The Lord of the Rings: Tales of Middle-Earth Commander]`), while Moxfield/ManaBox exports carry set codes (`LTC`). Resolving names to codes is what makes exact-printing matching (Task 9) work.

- [ ] **Step 1: Write the failing test** — `tests/test_sets.py`

```python
from app.sets import SetResolver, normalize_set_name


class FakeHttp:
    def __init__(self, payload=None, raise_error=False):
        self._payload = payload or {}
        self._raise = raise_error
        self.calls = 0

    def get_json(self, url, params=None):
        self.calls += 1
        if self._raise:
            raise RuntimeError("boom")
        return self._payload

    def get_text(self, url, params=None):
        return ""


SETS = {"data": [{"code": "LTC", "name": "The Lord of the Rings: Tales of Middle-Earth Commander"}]}


def test_normalize_set_name_collapses_whitespace_and_case():
    assert normalize_set_name("  The   Lord of the Rings ") == "the lord of the rings"


def test_code_for_resolves_scryfall_name_to_lowercase_code():
    resolver = SetResolver(FakeHttp(SETS))
    assert resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander") == "ltc"


def test_code_for_returns_none_for_unknown_name():
    resolver = SetResolver(FakeHttp(SETS))
    assert resolver.code_for("Some Unknown Set") is None


def test_code_for_returns_none_on_http_error():
    resolver = SetResolver(FakeHttp(raise_error=True))
    assert resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander") is None


def test_sets_are_fetched_once():
    http = FakeHttp(SETS)
    resolver = SetResolver(http)
    resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander")
    resolver.code_for("The Lord of the Rings: Tales of Middle-Earth Commander")
    assert http.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sets'`.

- [ ] **Step 3: Write minimal implementation** — `app/sets.py`

```python
from __future__ import annotations

SCYRSFALL_SETS_URL = "https://api.scryfall.com/sets"


def normalize_set_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


class SetResolver:
    """Maps Scryfall set names to set codes.

    Fetches the full set list once per instance and caches the name -> code
    mapping in memory. HTTP failures degrade to an empty map (no matches)
    rather than raising, so a Scryfall outage cannot break a store lookup.
    """

    def __init__(self, http):
        self._http = http
        self._by_name: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._by_name is None:
            try:
                payload = self._http.get_json(SCYRSFALL_SETS_URL)
            except Exception:
                self._by_name = {}
            else:
                mapping: dict[str, str] = {}
                for item in payload.get("data", []):
                    name = item.get("name")
                    code = item.get("code")
                    if name and code:
                        mapping[normalize_set_name(name)] = str(code).lower()
                self._by_name = mapping
        return self._by_name

    def code_for(self, set_name: str) -> str | None:
        return self._load().get(normalize_set_name(set_name))
```

- [ ] **Step 4: Add lookup tests to `tests/test_shopify_parse.py`**

```python
def test_parse_printing_hint_resolves_set_name_with_lookup():
    lookup = {"the lord of the rings: tales of middle-earth commander": "ltc"}.get
    assert parse_printing_hint(
        "Sol Ring (409) [The Lord of the Rings: Tales of Middle-Earth Commander]", lookup
    ) == "ltc:409"


def test_parse_printing_hint_returns_none_when_lookup_fails():
    assert parse_printing_hint("Sol Ring (409) [Unknown Set]", lambda name: None) is None
```

- [ ] **Step 5: Modify `parse_printing_hint` in `app/adapters/shopify_parse.py`** (replace the existing function; the default keeps Task 6 behavior)

```python
def parse_printing_hint(text: str, set_code_lookup=None) -> str | None:
    number = _NUMBER_RE.search(text)
    set_match = _SET_RE.search(text)
    if not (number and set_match):
        return None
    raw = set_match.group(1).strip()
    if set_code_lookup is not None:
        code = set_code_lookup(raw)
        if not code:
            return None
        code = code.lower()
    else:
        code = raw.lower()
    return f"{code}:{number.group(1).strip().lower()}"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_sets.py tests/test_shopify_parse.py -v`
Expected: PASS (9 passed).

- [ ] **Step 7: Run the whole suite and commit**

Run: `.venv/bin/pytest -v`

```bash
git add app/sets.py tests/test_sets.py app/adapters/shopify_parse.py tests/test_shopify_parse.py
git commit -m "feat: scryfall set-name resolver and printing hint normalization"
```

---

### Task 8: ShopifyAdapter base and Grey Ogre adapter (with contract test)

**Files:**
- Create: `app/adapters/shopify.py`
- Modify: `app/adapters/greyogre.py`
- Create: `tests/fixtures/greyogre/suggest_sol_ring.json`
- Create: `tests/fixtures/greyogre/scryfall_sets.json`
- Create: `tests/test_shopify_adapter.py`

**Interfaces:**
- Consumes: `StoreConfig` (Task 2), `CardRequest`, `Offer`, `AdapterError` (Task 1), `HttpClient` (Task 5), parsing helpers (Task 6), `SetResolver` (Task 7).
- Produces:
  - `ShopifyAdapter(config, http, sets=None)` with class/instance attrs `store_id, store_name, zone, needs_browser=False` and:
    - `search(card: CardRequest) -> list[Offer]`
    - `fetch_products(query: str) -> list[dict]` (calls `{base_url}/search/suggest.json`)
    - `_product_to_offers(product: dict) -> list[Offer]`
  - `GreyOgreAdapter(ShopifyAdapter)` — subclass with `store_id`/`store_name`/`zone` from config; equals base behavior for v1.

When `sets` is omitted, the adapter builds its own `SetResolver(http)` so it can turn bracketed set names into codes for `printing_hint`.

**The adapter does not filter by printing.** It returns offers for every name-matching product, each carrying its own `printing_hint`; dropping offers from other printings is `matching.py`'s job (Task 9). The fixture deliberately contains two printings so the contract test pins that behavior.

- [ ] **Step 1: Create fixture** — `tests/fixtures/greyogre/suggest_sol_ring.json`

```json
{
  "resources": {
    "results": {
      "products": [
        {
          "title": "Sol Ring (409) [The Lord of the Rings: Tales of Middle-Earth Commander]",
          "handle": "sol-ring-409-ltc-commander",
          "url": "/products/sol-ring-409-ltc-commander",
          "available": true,
          "variants": [
            {"title": "Near Mint", "price": "4.50", "available": true},
            {"title": "Near Mint Foil", "price": "9.00", "available": true},
            {"title": "Lightly Played", "price": "3.60", "available": false}
          ]
        },
        {
          "title": "Sol Ring (410) [The Lord of the Rings: Tales of Middle-Earth Commander]",
          "handle": "sol-ring-410-ltc-commander",
          "url": "/products/sol-ring-410-ltc-commander",
          "available": true,
          "variants": [
            {"title": "Near Mint", "price": "5.00", "available": true}
          ]
        }
      ]
    }
  }
}
```

Also create `tests/fixtures/greyogre/scryfall_sets.json` (used by the test's fake HTTP to resolve the bracketed set name to a code):

```json
{
  "data": [
    {
      "code": "LTC",
      "name": "The Lord of the Rings: Tales of Middle-Earth Commander"
    }
  ]
}
```

- [ ] **Step 2: Write the failing test** — `tests/test_shopify_adapter.py`

```python
import json
from pathlib import Path

from app.adapters.base import CardRequest
from app.adapters.registry import StoreConfig
from app.adapters.shopify import ShopifyAdapter
from app.adapters.greyogre import GreyOgreAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "greyogre"
PRODUCTS = FIXTURES / "suggest_sol_ring.json"
SETS = FIXTURES / "scryfall_sets.json"


class FixtureHttp:
    """Fake client: serves the product payload for search calls and the set
    list for Scryfall /sets calls. Records product calls only."""

    def __init__(self, products):
        self._products = products
        self._sets = json.loads(SETS.read_text())
        self.calls = []

    def get_json(self, url, params=None):
        if url.endswith("/sets"):
            return self._sets
        self.calls.append((url, params))
        return self._products

    def get_text(self, url, params=None):
        return ""


def _config():
    return StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )


def test_search_maps_variants_to_offers():
    http = FixtureHttp(json.loads(PRODUCTS.read_text()))
    adapter = ShopifyAdapter(_config(), http)
    offers = adapter.search(CardRequest(name="Sol Ring", set_code="LTC", collector_number="409"))

    assert len(offers) == 4
    nm, foil, lp, other = offers
    assert nm.store_id == "greyogre"
    assert str(nm.price_sgd) == "4.50"
    assert nm.condition == "NM"
    assert nm.finish == "nonfoil"
    assert nm.in_stock is True
    assert nm.url == "https://www.greyogregames.com/products/sol-ring-409-ltc-commander"

    assert foil.finish == "foil"
    assert str(foil.price_sgd) == "9.00"

    assert lp.condition == "LP"
    assert lp.in_stock is False

    # bracketed set name resolved to a code via the Scryfall set resolver
    assert nm.printing_hint == "ltc:409"

    # offers from the other printing are returned unfiltered — matching.py (Task 9)
    # is responsible for dropping them
    assert other.printing_hint == "ltc:410"
    assert str(other.price_sgd) == "5.00"

    # the search query was the card name
    assert http.calls[0][1]["q"] == "Sol Ring"


def test_greyogre_adapter_is_a_shopify_adapter():
    adapter = GreyOgreAdapter(_config(), FixtureHttp({"resources": {"results": {"products": []}}}))
    assert isinstance(adapter, ShopifyAdapter)
    assert adapter.store_id == "greyogre"
    assert adapter.zone == "singapore"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_shopify_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.shopify'`.

- [ ] **Step 4: Write minimal implementation** — `app/adapters/shopify.py`

```python
from __future__ import annotations

from urllib.parse import urljoin

from app.adapters.base import AdapterError, CardRequest, Offer
from app.adapters.shopify_parse import (
    normalize_name,
    parse_condition,
    parse_finish,
    parse_price,
    parse_printing_hint,
)
from app.sets import SetResolver


class ShopifyAdapter:
    """Base adapter for stores running on Shopify.

    Uses the storefront search suggestion endpoint, which returns products with
    their variants (condition / finish / price / availability) in one request.
    """

    needs_browser = False

    def __init__(self, config, http, sets=None):
        self.store_id = config.id
        self.store_name = config.name
        self.zone = config.zone
        self.base_url = config.base_url.rstrip("/")
        self._config = config
        self._http = http
        self._sets = sets or SetResolver(http)

    @property
    def search_url(self) -> str:
        return f"{self.base_url}/search/suggest.json"

    def fetch_products(self, query: str) -> list[dict]:
        params = {
            "q": query,
            "resources[type]": "product",
            "resources[limit]": "10",
            "resources[options][unavailable_products]": "last",
        }
        try:
            payload = self._http.get_json(self.search_url, params=params)
        except Exception as exc:  # HttpError and others bubble as AdapterError
            raise AdapterError(f"{self.store_id}: search failed for {query!r}: {exc}") from exc
        return (
            payload.get("resources", {})
            .get("results", {})
            .get("products", [])
        )

    def _product_to_offers(self, product: dict) -> list[Offer]:
        title = product.get("title", "")
        handle = product.get("handle", "")
        url = product.get("url") or f"/products/{handle}"
        absolute = urljoin(self.base_url + "/", url)
        printing_hint = parse_printing_hint(title, self._sets.code_for)
        offers: list[Offer] = []
        for variant in product.get("variants", []):
            variant_title = variant.get("title", "")
            combined = f"{title} {variant_title}"
            finish = parse_finish(combined)
            if finish == "unknown":
                # variant titles with no finish keyword (e.g. plain "Near Mint")
                # are non-foil by card-store convention
                finish = "nonfoil"
            offers.append(Offer(
                store_id=self.store_id,
                store_name=self.store_name,
                title=title,
                price_sgd=parse_price(variant.get("price", "0")),
                currency="SGD",
                condition=parse_condition(combined),
                finish=parse_finish(combined),
                in_stock=bool(variant.get("available", False)),
                url=absolute,
                printing_hint=printing_hint,
            ))
        return offers

    def search(self, card: CardRequest) -> list[Offer]:
        products = self.fetch_products(card.name)
        wanted = normalize_name(card.name)
        offers: list[Offer] = []
        for product in products:
            if normalize_name(product.get("title", "")) != wanted:
                continue
            offers.extend(self._product_to_offers(product))
        return offers
```

- [ ] **Step 5: Replace the stub** — `app/adapters/greyogre.py`

```python
from __future__ import annotations

from app.adapters.shopify import ShopifyAdapter


class GreyOgreAdapter(ShopifyAdapter):
    """Grey Ogre Games runs on Shopify; base behavior is sufficient for v1."""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_shopify_adapter.py tests/test_registry.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add app/adapters/shopify.py app/adapters/greyogre.py tests/fixtures/greyogre tests/test_shopify_adapter.py
git commit -m "feat: shopify adapter base and grey ogre store"
```

---

### Task 9: Matching rules

**Files:**
- Create: `app/matching.py`
- Create: `tests/test_matching.py`

**Interfaces:**
- Consumes: `CardRequest`, `Offer` (Task 1), `normalize_name` (Task 6).
- Produces:
  - `MatchConfidence(str, Enum)` with `EXACT`, `NAME_ONLY`, `AMBIGUOUS`
  - `MatchResult(card, offer, confidence)` with `to_dict() -> dict`
  - `match(card: CardRequest, offers: list[Offer]) -> list[MatchResult]`

Rules:
1. If `card.set_code` and `card.collector_number` are set:
   - an offer with `printing_hint == "{set}:{collector}"` -> `EXACT`
   - an offer with a different non-null `printing_hint` -> dropped
   - an offer with `printing_hint is None` -> `NAME_ONLY` (kept, lower confidence)
2. If the card has no printing:
   - offers are `NAME_ONLY` if they share a single distinct printing_hint (or none)
   - `AMBIGUOUS` if offers carry more than one distinct printing_hint

- [ ] **Step 1: Write the failing test** — `tests/test_matching.py`

```python
from decimal import Decimal

from app.adapters.base import CardRequest, Offer
from app.matching import MatchConfidence, match


def _offer(price, hint=None, title="Sol Ring"):
    return Offer(
        store_id="s", store_name="S", title=title,
        price_sgd=Decimal(str(price)), currency="SGD",
        condition="NM", finish="nonfoil", in_stock=True,
        url="https://x/p", printing_hint=hint,
    )


def test_exact_printing_matches_and_drops_other_printings():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    offers = [_offer("4.50", "ltc:409"), _offer("5.00", "ltc:410")]
    results = match(card, offers)
    assert len(results) == 1
    assert results[0].confidence == MatchConfidence.EXACT


def test_exact_printing_keeps_hintless_offer_as_name_only():
    card = CardRequest(name="Sol Ring", set_code="LTC", collector_number="409")
    results = match(card, [_offer("4.50", None)])
    assert results[0].confidence == MatchConfidence.NAME_ONLY


def test_name_only_card_single_printing_is_name_only():
    card = CardRequest(name="Sol Ring")
    results = match(card, [_offer("4.50", "ltc:409"), _offer("4.00", "ltc:409")])
    assert all(r.confidence == MatchConfidence.NAME_ONLY for r in results)


def test_name_only_card_multiple_printings_is_ambiguous():
    card = CardRequest(name="Sol Ring")
    results = match(card, [_offer("4.50", "ltc:409"), _offer("5.00", "ltc:410")])
    assert all(r.confidence == MatchConfidence.AMBIGUOUS for r in results)


def test_match_result_to_dict():
    card = CardRequest(name="Sol Ring")
    data = match(card, [_offer("4.50", "ltc:409")])[0].to_dict()
    assert data["confidence"] == "name_only"
    assert data["offer"]["price_sgd"] == "4.50"
    assert data["card"]["name"] == "Sol Ring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_matching.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.matching'`.

- [ ] **Step 3: Write minimal implementation** — `app/matching.py`

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_matching.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/matching.py tests/test_matching.py
git commit -m "feat: card/offer matching rules"
```

---

### Task 10: Scryfall validation client

**Files:**
- Create: `app/scryfall.py`
- Create: `tests/test_scryfall.py`

**Interfaces:**
- Consumes: `HttpClient` (Task 5).
- Produces: `ScryfallClient(http)` with:
  - `validate_printing(set_code: str, collector_number: str) -> bool`
  - `find_printings(name: str) -> list[dict]` returning `{"set": str, "collector_number": str}` entries (lowercased set)

Uses `https://api.scryfall.com/cards/{set}/{collector}` and `https://api.scryfall.com/cards/search?q=!"{name}"&unique=prints`. On any `HttpError`, returns `False` / `[]` rather than raising (best-effort enrichment).

- [ ] **Step 1: Write the failing test** — `tests/test_scryfall.py`

```python
from app.http import HttpError
from app.scryfall import ScryfallClient


class FakeHttp:
    def __init__(self, json_payload=None, raise_error=False):
        self._payload = json_payload or {}
        self._raise = raise_error

    def get_json(self, url, params=None):
        if self._raise:
            raise HttpError("boom")
        return self._payload

    def get_text(self, url, params=None):
        return ""


def test_validate_printing_true_when_card_returned():
    client = ScryfallClient(FakeHttp({"set": "ltc", "collector_number": "409"}))
    assert client.validate_printing("LTC", "409") is True


def test_validate_printing_false_on_error():
    client = ScryfallClient(FakeHttp(raise_error=True))
    assert client.validate_printing("LTC", "409") is False


def test_find_printings_maps_set_and_number():
    payload = {"data": [{"set": "LTC", "collector_number": "409"}]}
    assert ScryfallClient(FakeHttp(payload)).find_printings("Sol Ring") == [
        {"set": "ltc", "collector_number": "409"}
    ]


def test_find_printings_empty_on_error():
    assert ScryfallClient(FakeHttp(raise_error=True)).find_printings("Sol Ring") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scryfall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scryfall'`.

- [ ] **Step 3: Write minimal implementation** — `app/scryfall.py`

```python
from __future__ import annotations

from app.http import HttpError

API = "https://api.scryfall.com"


class ScryfallClient:
    def __init__(self, http):
        self._http = http

    def validate_printing(self, set_code: str, collector_number: str) -> bool:
        url = f"{API}/cards/{set_code.strip().lower()}/{collector_number.strip().lower()}"
        try:
            payload = self._http.get_json(url)
        except HttpError:
            return False
        return bool(payload.get("set"))

    def find_printings(self, name: str) -> list[dict]:
        url = f"{API}/cards/search"
        params = {"q": f'!"{name}"', "unique": "prints"}
        try:
            payload = self._http.get_json(url, params=params)
        except HttpError:
            return []
        return [
            {"set": str(card.get("set", "")).lower(),
             "collector_number": str(card.get("collector_number", "")).lower()}
            for card in payload.get("data", [])
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scryfall.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/scryfall.py tests/test_scryfall.py
git commit -m "feat: best-effort scryfall validation client"
```

---

### Task 11: Orchestrator (fan-out, cache, streaming events)

**Files:**
- Create: `app/orchestrator.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `CardRequest`, `Offer`, `AdapterError` (Task 1), `Database` (Task 3), `match` (Task 9).
- Produces: `async def run_search(cards, adapters, db, cache_ttl_seconds=21600, force_refresh=False) -> AsyncIterator[dict]`.

Event shapes emitted (one `dict` per SSE `data:` line):
- `{"type": "started", "total": int, "card_count": int}`
- `{"type": "result", "card": {...}, "store_id": str, "store_name": str, "cached": bool, "offers": [MatchResult.to_dict()...], "error": None}`
- `{"type": "error", "card": {...}, "store_id": str, "message": str}`
- `{"type": "done", "summary": {"cards": int, "pairs": int, "offers": int, "errors": int}}`

`run_search`:
- Caches **raw offers** (not matches) as `{"offers": [offer.to_dict()...]}` under key `(store_id, card.key)`.
- Uses `asyncio.to_thread` for each adapter call, bounded by `max(len(adapters) * cfg.concurrency)`.
- Never raises on an adapter failure; emits an `error` event instead.

- [ ] **Step 1: Write the failing test** — `tests/test_orchestrator.py`

```python
import asyncio
from decimal import Decimal

from app.adapters.base import AdapterError, CardRequest, Offer
from app.db import Database
from app.orchestrator import run_search


class FakeAdapter:
    store_id = "fake"
    store_name = "Fake Store"
    zone = "singapore"
    needs_browser = False

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def search(self, card):
        self.calls += 1
        if self.fail:
            raise AdapterError("nope")
        return [Offer(
            store_id="fake", store_name="Fake Store", title=card.name,
            price_sgd=Decimal("2.50"), currency="SGD", condition="NM",
            finish="nonfoil", in_stock=True, url="https://x/p",
            printing_hint=None,
        )]


def _run(cards, adapters, db, **kwargs):
    async def collect():
        return [e async for e in run_search(cards, adapters, db, **kwargs)]
    return asyncio.run(collect())


def test_run_search_emits_results_and_done(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    events = _run([CardRequest(name="Sol Ring")], [FakeAdapter()], db)
    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert "result" in types
    assert types[-1] == "done"
    result = next(e for e in events if e["type"] == "result")
    assert result["offers"][0]["offer"]["price_sgd"] == "2.50"
    assert events[-1]["summary"]["errors"] == 0


def test_run_search_isolates_adapter_failure(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    events = _run([CardRequest(name="Sol Ring")], [FakeAdapter(fail=True)], db)
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["summary"]["errors"] == 1


def test_run_search_uses_cache_on_second_call(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    adapter = FakeAdapter()
    _run([CardRequest(name="Sol Ring")], [adapter], db)
    assert adapter.calls == 1
    events = _run([CardRequest(name="Sol Ring")], [adapter], db)
    assert adapter.calls == 1  # served from cache
    result = next(e for e in events if e["type"] == "result")
    assert result["cached"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.orchestrator'`.

- [ ] **Step 3: Write minimal implementation** — `app/orchestrator.py`

```python
from __future__ import annotations

import asyncio
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
    return Offer(
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
    )


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
                cached = await asyncio.to_thread(
                    db.get_cached, adapter.store_id, cache_key, cache_ttl_seconds
                )
                if cached is not None:
                    offers = [_offer_from_dict(d) for d in cached.get("offers", [])]
                    return card, adapter, offers, True, None
            try:
                offers = await asyncio.to_thread(adapter.search, card)
            except AdapterError as exc:
                return card, adapter, [], False, str(exc)
            except Exception as exc:  # defensive: never abort the run
                return card, adapter, [], False, f"unexpected: {exc}"
            await asyncio.to_thread(
                db.set_cached, adapter.store_id, cache_key,
                {"offers": [o.to_dict() for o in offers]},
            )
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: async orchestrator with cache and streaming events"
```

---

### Task 12: FastAPI app, run registry, and SSE endpoint

**Files:**
- Create: `app/runs.py`
- Create: `app/main.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `load_config`, `build_adapters` (Task 2), `Database` (Task 3), `parse_cards` (Task 4), `HttpxClient` (Task 5), `run_search` (Task 11).
- Produces:
  - `RunRegistry` with `create(cards, adapters, db, force_refresh) -> str` (starts a background task and returns `run_id`), `get(run_id) -> Run | None`.
  - `Run` with `events: list[dict]`, `done: bool`, and `async def subscribe() -> AsyncIterator[dict]` (replays existing events, then yields new ones until done).
  - `create_app(config_path="config/stores.yaml", db_path="mtgcardtracker.db") -> FastAPI` with routes:
    - `GET /api/stores` -> `{"zones": [...], "stores": [...]}`
    - `POST /api/runs` body `{"text": str, "store_ids": [str], "force_refresh": bool}` -> `{"run_id": str, "card_count": int}`
    - `GET /api/runs/{run_id}/stream` -> `text/event-stream`
    - `GET /api/lists`, `POST /api/lists`, `GET /api/lists/{id}`, `DELETE /api/lists/{id}`

- [ ] **Step 1: Write the failing test** — `tests/test_api.py`

```python
from fastapi.testclient import TestClient

from app.main import create_app


def _client(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text(
        "zones:\n  - id: singapore\n    name: Singapore\n"
        "stores:\n"
        "  - id: greyogre\n    name: Grey Ogre Games\n    zone: singapore\n"
        "    adapter: app.adapters.greyogre:GreyOgreAdapter\n"
        "    base_url: https://example.test\n    enabled: true\n"
    )
    app = create_app(config_path=str(stores), db_path=str(tmp_path / "t.db"))
    return TestClient(app)


def test_stores_endpoint(tmp_path):
    client = _client(tmp_path)
    data = client.get("/api/stores").json()
    assert data["zones"][0]["id"] == "singapore"
    assert data["stores"][0]["id"] == "greyogre"


def test_saved_lists_roundtrip(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/lists", json={"name": "Deck", "text": "1 Sol Ring"}).json()
    assert created["id"] > 0
    listing = client.get("/api/lists").json()
    assert listing[0]["name"] == "Deck"
    got = client.get(f"/api/lists/{created['id']}").json()
    assert got["raw_text"] == "1 Sol Ring"
    client.delete(f"/api/lists/{created['id']}")
    assert client.get("/api/lists").json() == []


class FakeHttp:
    def get_json(self, url, params=None):
        return {"resources": {"results": {"products": []}}}

    def get_text(self, url, params=None):
        return ""


def test_run_stream_emits_done(tmp_path, monkeypatch):
    import app.main as main_module

    class FakeAdapter:
        store_id = "greyogre"
        store_name = "Grey Ogre Games"
        zone = "singapore"
        needs_browser = False
        concurrency = 1

        def search(self, card):
            return []

    monkeypatch.setattr(main_module, "build_adapters", lambda configs, http: [FakeAdapter()])
    monkeypatch.setattr(main_module, "_make_http", lambda: FakeHttp())
    client = _client(tmp_path)
    run = client.post(
        "/api/runs",
        json={"text": "1 Sol Ring", "store_ids": ["greyogre"], "force_refresh": True},
    ).json()
    assert run["card_count"] == 1
    with client.stream("GET", f"/api/runs/{run['run_id']}/stream") as response:
        body = "".join(response.iter_text())
    assert '"type": "done"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Write minimal implementation** — `app/runs.py`

```python
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator


class Run:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.events: list[dict] = []
        self.done = False
        self._condition = asyncio.Condition()

    async def publish(self, event: dict) -> None:
        async with self._condition:
            self.events.append(event)
            if event.get("type") == "done":
                self.done = True
            self._condition.notify_all()

    async def subscribe(self) -> AsyncIterator[dict]:
        index = 0
        while True:
            async with self._condition:
                while index >= len(self.events) and not self.done:
                    await self._condition.wait()
                batch = self.events[index:]
                index = len(self.events)
                finished = self.done and index >= len(self.events)
            for event in batch:
                yield event
            if finished:
                return


class RunRegistry:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def create(self, cards, adapters, db, force_refresh: bool = False) -> Run:
        from app.orchestrator import run_search

        run = Run()
        self._runs[run.id] = run

        async def _drive():
            async for event in run_search(cards, adapters, db, force_refresh=force_refresh):
                await run.publish(event)

        task = asyncio.create_task(_drive())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run
```

- [ ] **Step 4: Write minimal implementation** — `app/main.py`

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/runs.py app/main.py tests/test_api.py
git commit -m "feat: fastapi app with runs, sse stream, and saved lists"
```

---

### Task 13: Frontend input screen

**Files:**
- Create: `web/index.html`
- Create: `web/styles.css`
- Create: `web/app.js`
- Create: `tests/test_web.py`

**Interfaces:**
- Consumes: `GET /api/stores`, `POST /api/runs`.
- Produces: a page with a textarea, a store selector, a "Search" button, and a results table skeleton.

- [ ] **Step 1: Write the failing test** — `tests/test_web.py`

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_index_served(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    response = client.get("/")
    assert response.status_code == 200
    assert "MTG Card Tracker" in response.text


def test_app_js_served(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    assert client.get("/app.js").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py -v`
Expected: FAIL — 404 because `web/` does not exist yet.

- [ ] **Step 3: Write `web/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MTG Card Tracker</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header><h1>MTG Card Tracker</h1><span id="status"></span></header>
  <main>
    <section id="input-panel">
      <label for="card-list">Card list (Moxfield / ManaBox / plain text)</label>
      <textarea id="card-list" rows="12" placeholder="1 Sol Ring&#10;2x Llanowar Elves"></textarea>
      <fieldset id="store-picker"><legend>Stores</legend></fieldset>
      <label class="inline"><input type="checkbox" id="force-refresh" /> Force refresh (ignore cache)</label>
      <button id="search-button">Search</button>
    </section>
    <section id="results-panel">
      <div id="summary"></div>
      <table id="results">
        <thead><tr><th>Card</th><th>Best offer</th><th>Offers</th></tr></thead>
        <tbody id="results-body"></tbody>
      </table>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `web/styles.css`** (concise, no framework)

```css
:root { font-family: system-ui, sans-serif; }
body { margin: 0; background: #0f1115; color: #e6e6e6; }
header { display: flex; gap: 12px; align-items: baseline; padding: 12px 20px; background: #171a21; }
main { display: grid; grid-template-columns: 360px 1fr; gap: 20px; padding: 20px; }
textarea { width: 100%; font-family: ui-monospace, monospace; background: #0b0d10; color: inherit; border: 1px solid #333; border-radius: 6px; padding: 8px; }
fieldset { border: 1px solid #333; border-radius: 6px; margin-top: 12px; }
button { margin-top: 12px; padding: 8px 16px; border-radius: 6px; border: 0; background: #4f7cff; color: white; cursor: pointer; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid #222; vertical-align: top; }
.offer { display: block; }
.offer.best { color: #7ddc81; font-weight: 600; }
.confidence { color: #999; font-size: 0.85em; }
```

- [ ] **Step 5: Write `web/app.js`** (input + streaming client)

```javascript
const statusEl = document.getElementById("status");
const storePicker = document.getElementById("store-picker");
const searchButton = document.getElementById("search-button");
const summaryEl = document.getElementById("summary");
const resultsBody = document.getElementById("results-body");

let stores = [];
let cards = new Map(); // key -> { card, offers: [] }

async function loadStores() {
  const response = await fetch("/api/stores");
  const data = await response.json();
  stores = data.stores.filter((s) => s.enabled);
  for (const store of stores) {
    const label = document.createElement("label");
    label.className = "inline";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = store.id;
    checkbox.checked = true;
    label.append(checkbox, document.createTextNode(` ${store.name}`));
    storePicker.append(label);
  }
}

function selectedStoreIds() {
  return [...storePicker.querySelectorAll("input:checked")].map((c) => c.value);
}

function render() {
  resultsBody.innerHTML = "";
  for (const { card, offers } of cards.values()) {
    const row = document.createElement("tr");
    const sorted = [...offers].sort((a, b) => Number(a.offer.price_sgd) - Number(b.offer.price_sgd));
    const best = sorted[0];
    const cardCell = document.createElement("td");
    cardCell.textContent = card.name;
    const bestCell = document.createElement("td");
    if (best) {
      bestCell.innerHTML = `<span class="offer best">$${best.offer.price_sgd} ${best.offer.condition ?? ""} ${best.offer.finish}</span>`;
    } else {
      bestCell.textContent = "—";
    }
    const offersCell = document.createElement("td");
    for (const match of sorted) {
      const line = document.createElement("a");
      line.className = "offer";
      line.href = match.offer.url;
      line.target = "_blank";
      line.textContent = `${match.offer.store_name}: $${match.offer.price_sgd} ${match.offer.condition ?? ""} ${match.offer.finish}${match.offer.in_stock ? "" : " (out of stock)"}`;
      const meta = document.createElement("div");
      meta.className = "confidence";
      meta.textContent = match.confidence;
      const wrap = document.createElement("div");
      wrap.append(line, meta);
      offersCell.append(wrap);
    }
    row.append(cardCell, bestCell, offersCell);
    resultsBody.append(row);
  }
}

function upsertResult(event) {
  const entry = cards.get(event.card.key) || { card: event.card, offers: [] };
  entry.offers.push(...event.offers);
  cards.set(event.card.key, entry);
  render();
}

async function startSearch() {
  cards = new Map();
  resultsBody.innerHTML = "";
  summaryEl.textContent = "";
  const text = document.getElementById("card-list").value;
  const force = document.getElementById("force-refresh").checked;
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, store_ids: selectedStoreIds(), force_refresh: force }),
  });
  if (!response.ok) { statusEl.textContent = "Failed to start run"; return; }
  const { run_id, card_count } = await response.json();
  statusEl.textContent = `Searching ${card_count} cards…`;
  const source = new EventSource(`/api/runs/${run_id}/stream`);
  source.addEventListener("result", (e) => upsertResult(JSON.parse(e.data)));
  source.addEventListener("error", (e) => { try { console.warn("adapter error", JSON.parse(e.data)); } catch {} });
  source.addEventListener("done", (e) => {
    const data = JSON.parse(e.data);
    summaryEl.textContent = `${data.summary.offers} offers, ${data.summary.errors} errors`;
    statusEl.textContent = "Done";
    source.close();
  });
}

searchButton.addEventListener("click", startSearch);
loadStores();
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_web.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: web ui input screen and streaming results"
```

---

### Task 14: Condition/finish/stock filters and saved lists UI

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Create: `tests/test_filters.py` (pure-function tests for the filter logic)

To keep filter logic testable without a browser, extract it into `web/filters.js` and unit-test the pure function via a tiny Node-free approach: duplicate the pure function in Python is bad. Instead, implement filters server-side as a pure Python module and expose them in the UI.

**Files (revised):**
- Create: `app/filters.py`
- Create: `tests/test_filters.py`
- Modify: `web/app.js` (call `POST /api/filter` OR apply the same rules client-side)

- [ ] **Step 1: Write the failing test** — `tests/test_filters.py`

```python
from decimal import Decimal

from app.adapters.base import Offer
from app.filters import OfferFilters, filter_offers


def _offer(condition, finish, in_stock=True):
    return Offer(
        store_id="s", store_name="S", title="t",
        price_sgd=Decimal("1.00"), currency="SGD", condition=condition,
        finish=finish, in_stock=in_stock, url="https://x",
    )


def test_filter_in_stock_only():
    result = filter_offers([_offer("NM", "nonfoil", False)], OfferFilters(in_stock_only=True))
    assert result == []


def test_filter_conditions():
    offers = [_offer("NM", "nonfoil"), _offer("LP", "nonfoil")]
    result = filter_offers(offers, OfferFilters(conditions=["NM"]))
    assert [o.condition for o in result] == ["NM"]


def test_filter_finish():
    offers = [_offer("NM", "nonfoil"), _offer("NM", "foil")]
    result = filter_offers(offers, OfferFilters(finishes=["foil"]))
    assert [o.finish for o in result] == ["foil"]


def test_filters_are_and_combined():
    offers = [_offer("NM", "foil"), _offer("LP", "foil"), _offer("NM", "nonfoil")]
    result = filter_offers(offers, OfferFilters(conditions=["NM"], finishes=["foil"]))
    assert len(result) == 1
    assert result[0].condition == "NM" and result[0].finish == "foil"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_filters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.filters'`.

- [ ] **Step 3: Write minimal implementation** — `app/filters.py`

```python
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
```

- [ ] **Step 4: Add client-side mirror + controls in `web/`**

Add to `web/index.html` inside `#input-panel`, after `#store-picker`:

```html
<fieldset id="filters"><legend>Filters</legend>
  <label><input type="checkbox" id="filter-in-stock" /> In stock only</label>
  <label><input type="checkbox" id="filter-nm" checked /> Near Mint only</label>
  <label><input type="checkbox" id="filter-nonfoil" checked /> Non-foil only</label>
</fieldset>
```

In `web/app.js`, add a `applyFilters(offers)` function mirroring `app/filters.py` and call it inside `render()` before sorting:

```javascript
function applyFilters(offers) {
  const inStockOnly = document.getElementById("filter-in-stock").checked;
  const nmOnly = document.getElementById("filter-nm").checked;
  const nonfoilOnly = document.getElementById("filter-nonfoil").checked;
  return offers.filter((m) => {
    if (inStockOnly && !m.offer.in_stock) return false;
    if (nmOnly && m.offer.condition !== "NM") return false;
    if (nonfoilOnly && m.offer.finish !== "nonfoil") return false;
    return true;
  });
}
```

Change the first line of `render()`'s loop body to `const sorted = applyFilters(offers).sort(...)`. Re-render on filter change:

```javascript
for (const id of ["filter-in-stock", "filter-nm", "filter-nonfoil"]) {
  document.getElementById(id).addEventListener("change", render);
}
```

Also add a **Saved lists** panel to `web/index.html` and wire `GET/POST/DELETE /api/lists` in `app.js`.

```html
<section id="lists-panel">
  <h2>Saved lists</h2>
  <input id="list-name" placeholder="List name" />
  <button id="save-list">Save current</button>
  <ul id="saved-lists"></ul>
</section>
```

```javascript
async function loadLists() {
  const lists = await (await fetch("/api/lists")).json();
  const ul = document.getElementById("saved-lists");
  ul.innerHTML = "";
  for (const list of lists) {
    const li = document.createElement("li");
    const load = document.createElement("button");
    load.textContent = list.name;
    load.onclick = async () => {
      const saved = await (await fetch(`/api/lists/${list.id}`)).json();
      document.getElementById("card-list").value = saved.raw_text;
    };
    const del = document.createElement("button");
    del.textContent = "✕";
    del.onclick = async () => { await fetch(`/api/lists/${list.id}`, { method: "DELETE" }); loadLists(); };
    li.append(load, del);
    ul.append(li);
  }
}

document.getElementById("save-list").onclick = async () => {
  const name = document.getElementById("list-name").value || "Untitled";
  await fetch("/api/lists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, text: document.getElementById("card-list").value }),
  });
  loadLists();
};
loadLists();
```

Note: the `app.filters` module is the source of truth; the JS mirror exists only to avoid a round trip per filter change. The Python tests pin behavior.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_filters.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add app/filters.py tests/test_filters.py web
git commit -m "feat: condition/finish/stock filters and saved-lists ui"
```

---

### Task 15: Developer CLI (`explore`, `scaffold`, `probe`, `validate`)

**Files:**
- Create: `app/devtools.py`
- Create: `cli.py`
- Create: `tests/test_devtools.py`

**Interfaces:**
- Consumes: `detect_format` (Task 4), `HttpxClient` (Task 5), `load_config`/`build_adapters` (Task 2).
- Produces:
  - `detect_platform(base_url: str, http) -> str` returning `"shopify"`, `"custom"`, or `"unknown"` (probes `/products.json`; a JSON body with a top-level `"products"` key means Shopify, a bare JSON list means `"other"`, non-JSON means `"custom"`).
  - `dump_for_inspection(url, http, out_dir) -> Path` writes the raw response to a file.
  - `cli.py` argparse with subcommands `explore`, `scaffold`, `probe`, `validate`.

- [ ] **Step 1: Write the failing test** — `tests/test_devtools.py`

```python
import httpx
import pytest

from app.devtools import detect_platform
from app.http import HttpxClient


def _http(handler):
    return HttpxClient("mtgcardtracker/0.1", transport=httpx.MockTransport(handler))


def test_detect_shopify_from_products_json():
    def handler(request):
        return httpx.Response(200, json={"products": []})

    assert detect_platform("https://x.test", _http(handler)) == "shopify"


def test_detect_other_for_bare_list():
    payload = [{"id": 1, "url": "https://x.test/products/a.json"}]

    def handler(request):
        return httpx.Response(200, json=payload)

    assert detect_platform("https://x.test", _http(handler)) == "other"


def test_detect_custom_when_not_json():
    def handler(request):
        return httpx.Response(200, text="<html></html>")

    assert detect_platform("https://x.test", _http(handler)) == "custom"


def test_detect_custom_on_404():
    def handler(request):
        return httpx.Response(404)

    assert detect_platform("https://x.test", _http(handler)) == "custom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_devtools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.devtools'`.

- [ ] **Step 3: Write minimal implementation** — `app/devtools.py`

```python
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
```

- [ ] **Step 4: Write `cli.py`**

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.adapters.base import CardRequest
from app.adapters.registry import build_adapters, load_config
from app.devtools import detect_platform, dump_for_inspection
from app.http import HttpxClient


def _http() -> HttpxClient:
    return HttpxClient("mtgcardtracker/0.1 (+local tool)")


def cmd_explore(args):
    http = _http()
    platform = detect_platform(args.url, http)
    print(f"platform: {platform}")
    path = dump_for_inspection(args.url, http, args.out)
    print(f"dumped:   {path}")


def cmd_scaffold(args):
    stores = Path("config/stores.yaml")
    text = stores.read_text()
    entry = (
        f"  - id: {args.store_id}\n"
        f"    name: {args.store_id.title()}\n"
        f"    zone: singapore\n"
        f"    adapter: app.adapters.{args.store_id}:{args.store_id.title()}Adapter\n"
        f"    base_url: {args.base_url}\n"
        f"    enabled: false\n"
    )
    stores.write_text(text.rstrip() + "\n" + entry)
    print(f"Added disabled entry for {args.store_id} to {stores}")


def cmd_probe(args):
    configs = load_config("config/stores.yaml")
    adapters = build_adapters(configs, _http())
    adapter = next((a for a in adapters if a.store_id == args.store_id), None)
    if adapter is None:
        raise SystemExit(f"store {args.store_id!r} not found or disabled")
    card = CardRequest(name=args.card)
    offers = adapter.search(card)
    print(json.dumps([o.to_dict() for o in offers], indent=2))


def cmd_validate(args):
    from app.adapters.shopify_parse import parse_finish
    print("validate: fixture replay is exercised by pytest contract tests")
    print("run: pytest -v")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cli")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("explore")
    p.add_argument("url")
    p.add_argument("--out", default="fixtures/inspect")
    p.set_defaults(func=cmd_explore)

    p = sub.add_parser("scaffold")
    p.add_argument("store_id")
    p.add_argument("--base-url", required=True)
    p.add_argument("--platform", choices=["shopify", "custom"], default="shopify")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("probe")
    p.add_argument("store_id")
    p.add_argument("card")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("validate")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_devtools.py -v && python cli.py --help`
Expected: PASS (4 passed) and the argparse help prints.

- [ ] **Step 6: Run the whole suite and commit**

Run: `pytest -v`
Expected: all tests pass.

```bash
git add app/devtools.py cli.py tests/test_devtools.py
git commit -m "feat: developer cli for adapter discovery and probing"
```

---

## Milestone 6: Per-store adapter rollout (repeatable procedure)

This milestone is intentionally **not** a fixed task list because each store needs a live crawl before its adapter can be written. Repeat this procedure per store; each store is one task/PR with its own fixture and contract test.

1. **Recon:** `python cli.py explore https://<store>` and inspect the dump. For Shopify stores, confirm `/search/suggest.json` returns the products/variants shape used in Task 8.
2. **Classify:**
   - Shopify -> add `<store>.py` with `class <Name>Adapter(ShopifyAdapter): ...` (usually empty; add overrides only for quirks like a different search URL, a currency toggle, or variant titles that don't encode conditions).
   - Custom -> implement the `Adapter` protocol directly. Use `app.http.HttpClient`; if the site is JS-rendered, set `needs_browser = True` and implement a `BrowserSession` wrapper (add it in `app/browser.py` with a Playwright `sync_playwright` context). Register the `browser` argument.
3. **Record fixtures:** `python cli.py probe <store_id> "<representative card>"` or save the raw response manually into `tests/fixtures/<store_id>/`.
4. **Contract test:** add `tests/test_<store>_adapter.py` replaying the fixture and asserting offers (price, condition, finish, stock, printing hint). Add `@pytest.mark.live` for one opt-in real-network test guarded by `pytest.ini` markers.
5. **Enable** the store in `config/stores.yaml` (`enabled: true`) once its contract test passes.
6. **Commit** the adapter, fixture, config change, and test together.

Target order: Grey Ogre (done in Task 8) -> Games Haven -> Cards Citadel -> Hideout -> Card Arena -> Dueller's Point -> Agora Hobby (custom).

## Self-Review

**Spec coverage:** stack (Tasks 1,5,12), goals (Tasks 4,8,11,13), non-goals honored (no price history, no cart, no evasion — enforced by Global Constraints), architecture (Tasks 1-12), recon table (Task 15 explore + Milestone 6), data model (Tasks 1,3,9), adapter interface (Tasks 1,2,8), stores.yaml (Task 2), input parsing incl. plain text (Task 4), orchestration/cache/politeness (Tasks 5,11), SSE UI (Tasks 12,13,14), saved lists (Tasks 3,12,14), testing incl. fixtures/live marker (Tasks 7,8,15, Milestone 6), CLI (Task 15), Scryfall (Tasks 7 set resolver, 10 validation client), milestones 1-7 (mapped in order).

**Placeholder scan:** the only procedurally-generated content is Milestone 6, which is explicitly a repeatable recipe because it requires live recon — not a TBD in a fixed task. All fixed tasks contain real code.

**Type consistency:** `CardRequest`/`Offer`/`AdapterError` defined in Task 1 are reused verbatim. `StoreConfig` fields match `config/stores.yaml`. `build_adapters(configs, http, browser=None)` is called consistently in Tasks 2, 12, 15. `match()` returns `MatchResult` with `to_dict()` used by the orchestrator. `run_search` signature matches its callers in `app/runs.py`. `Database` method names match all call sites. `detect_platform` return values match the tests.
