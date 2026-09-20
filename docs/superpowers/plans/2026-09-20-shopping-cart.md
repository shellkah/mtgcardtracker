# Shopping Cart ("The Satchel") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent single shopping cart that saves specific offers from search results, groups them by store, totals them, and can re-check their prices/stock on demand — with no checkout automation.

**Architecture:** A new `cart_items` SQLite table stores offer snapshots; pure helpers in `app/cart.py` build rows, detect duplicate choices, group by store, and total; FastAPI routes in `app/main.py` expose CRUD and a refresh endpoint that starts a normal orchestrator run (client-driven reconciliation over the existing SSE stream). The front end adds a "Satchel" modal drawer and add-buttons on result offers, in plain HTML/CSS/JS.

**Tech Stack:** Python 3.11+, FastAPI, sqlite3 (stdlib), pytest, plain HTML/CSS/vanilla JS (no build step).

**Spec:** `docs/superpowers/specs/2026-09-20-shopping-cart-design.md`

## Global Constraints

- Python >= 3.11; run tests with `uv run pytest`.
- All prices are SGD; store `price_sgd` as TEXT to preserve `Decimal`.
- Frontend has **no build step** — plain HTML/CSS/JS using `EventSource`.
- Exactly **one** cart; no named carts/wishlists.
- No checkout or order automation — the cart only saves offers for manual purchase.
- Adding an offer whose `identity_key = store_id|title|condition|finish|url` already exists is a no-op (`created=false`), not an error.
- Different offers for the same card are both kept and flagged as "duplicate choice".
- Cart lines are excluded from subtotals/totals when `status != "ok"` or `in_stock == 0`; duplicate-choice lines ARE still counted.
- `POST /api/cart/refresh` starts a run with `force_refresh=True`.
- Unavailable lines keep their last-known price; only a successful re-check that misses the offer sets `status="unavailable"`.
- Follow existing repo conventions: snake_case JSON, TDD, one commit per task.

---

### Task 1: Cart domain helpers (`app/cart.py`)

**Files:**
- Create: `app/cart.py`
- Test: `tests/test_cart.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `identity_key(offer: dict) -> str`
  - `line_from_offer(card: dict, offer: dict) -> dict` — a `cart_items` row (without `added_at`, which the DB stamps)
  - `group_by_store(items: list[dict]) -> list[dict]` — each section `{store_id, store_name, items, subtotal_sgd}`
  - `duplicate_card_keys(items: list[dict]) -> set[str]`
  - `totals(items: list[dict]) -> dict` — `{"total_sgd": str, "excluded": int}`

- [ ] **Step 1: Write the failing test** — `tests/test_cart.py`

```python
from app.cart import (
    duplicate_card_keys,
    group_by_store,
    identity_key,
    line_from_offer,
    totals,
)


CARD = {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
        "collector_number": "409", "quantity": 2}


def _offer(**over):
    offer = {
        "store_id": "greyogre", "store_name": "Grey Ogre Games",
        "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
        "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
        "finish": "nonfoil", "in_stock": True, "printing_hint": "ltc:409",
        "image_url": None, "fetched_at": "2026-09-20T00:00:00+00:00",
    }
    offer.update(over)
    return offer


def _item(**over):
    item = line_from_offer(CARD, _offer())
    item["id"] = over.pop("id", 1)
    item["card_key"] = over.pop("card_key", CARD["key"])
    item["card_name"] = over.pop("card_name", CARD["name"])
    item["status"] = over.pop("status", "ok")
    item["in_stock"] = over.pop("in_stock", 1)
    item["price_sgd"] = over.pop("price_sgd", "4.50")
    item["quantity"] = over.pop("quantity", 2)
    item["store_id"] = over.pop("store_id", "greyogre")
    item["store_name"] = over.pop("store_name", "Grey Ogre Games")
    item.update(over)
    return item


def test_identity_key_ignores_price_and_stock():
    a = _offer(price_sgd="4.50", in_stock=True)
    b = _offer(price_sgd="9.99", in_stock=False)
    assert identity_key(a) == identity_key(b)


def test_identity_key_changes_with_condition():
    assert identity_key(_offer(condition="NM")) != identity_key(_offer(condition="LP"))


def test_line_from_offer_copies_card_and_offer_fields():
    row = line_from_offer(CARD, _offer())
    assert row["identity_key"] == identity_key(_offer())
    assert row["card_key"] == "ltc:409"
    assert row["card_name"] == "Sol Ring"
    assert row["quantity"] == 2
    assert row["price_sgd"] == "4.50"
    assert row["in_stock"] == 1
    assert row["status"] == "ok"
    assert "added_at" not in row


def test_line_from_offer_defaults_quantity_to_at_least_one():
    row = line_from_offer({**CARD, "quantity": 0}, _offer())
    assert row["quantity"] == 1


def test_group_by_store_groups_and_subtotals():
    a = _item(id=1, store_id="greyogre", price_sgd="4.50", quantity=2)
    b = _item(id=2, store_id="hideout", price_sgd="3.00", quantity=1)
    groups = group_by_store([a, b])
    assert [g["store_id"] for g in groups] == ["greyogre", "hideout"]
    assert groups[0]["subtotal_sgd"] == "9.00"
    assert groups[1]["subtotal_sgd"] == "3.00"


def test_group_by_store_excludes_unavailable_from_subtotal():
    ok = _item(id=1, price_sgd="4.50", quantity=2)
    gone = _item(id=2, price_sgd="100.00", status="unavailable")
    groups = group_by_store([ok, gone])
    assert groups[0]["subtotal_sgd"] == "9.00"
    assert len(groups[0]["items"]) == 2


def test_duplicate_card_keys_flags_cards_with_two_lines():
    a = _item(id=1, card_key="ltc:409")
    b = _item(id=2, card_key="ltc:409", store_id="hideout")
    c = _item(id=3, card_key="war:123")
    assert duplicate_card_keys([a, b, c]) == {"ltc:409"}


def test_totals_sums_countable_lines_and_counts_excluded():
    ok = _item(id=1, price_sgd="4.50", quantity=2)
    oos = _item(id=2, price_sgd="1.00", quantity=1, in_stock=0)
    gone = _item(id=3, price_sgd="2.00", quantity=1, status="unavailable")
    result = totals([ok, oos, gone])
    assert result["total_sgd"] == "9.00"
    assert result["excluded"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cart.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cart'`

- [ ] **Step 3: Write minimal implementation** — `app/cart.py`

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cart.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/cart.py tests/test_cart.py
git commit -m "feat: cart domain helpers (identity, grouping, totals)"
```

---

### Task 2: Cart storage (`app/db.py`)

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: a row dict from `app.cart.line_from_offer` (must contain `identity_key`, `card_key`, `card_name`, `set_code`, `collector_number`, `store_id`, `store_name`, `title`, `url`, `printing_hint`, `image_url`, `condition`, `finish`, `price_sgd`, `currency`, `in_stock`, `quantity`, `status`, `offer_fetched_at`).
- Produces (methods on `Database`):
  - `add_cart_item(item: dict) -> tuple[int, bool]`
  - `list_cart() -> list[dict]`
  - `get_cart_item(item_id: int) -> dict | None`
  - `update_cart_quantity(item_id: int, quantity: int) -> dict | None`
  - `update_cart_item(item_id: int, **fields) -> dict | None`
  - `remove_cart_item(item_id: int) -> None`
  - `clear_cart() -> None`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_db.py`

```python
from app.cart import line_from_offer


def _row(**over):
    row = line_from_offer(
        {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
         "collector_number": "409", "quantity": 2},
        {"store_id": "greyogre", "store_name": "Grey Ogre Games",
         "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
         "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
         "finish": "nonfoil", "in_stock": True,
         "fetched_at": "2026-09-20T00:00:00+00:00"},
    )
    row.update(over)
    return row


def test_cart_add_dedupes_identical_offers(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, created = db.add_cart_item(_row())
    assert created is True
    again_id, again_created = db.add_cart_item(_row())
    assert again_created is False
    assert again_id == item_id
    assert len(db.list_cart()) == 1
    db.close()


def test_cart_quantity_update_and_remove(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, _ = db.add_cart_item(_row())
    updated = db.update_cart_quantity(item_id, 5)
    assert updated["quantity"] == 5
    assert db.update_cart_quantity(item_id, 0)["quantity"] == 1  # clamped
    assert db.update_cart_quantity(9999, 2) is None
    db.remove_cart_item(item_id)
    assert db.list_cart() == []
    db.close()


def test_cart_refresh_update_and_clear(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, _ = db.add_cart_item(_row())
    updated = db.update_cart_item(
        item_id, status="unavailable", last_checked_at=123.0
    )
    assert updated["status"] == "unavailable"
    assert updated["price_sgd"] == "4.50"  # untouched
    assert updated["last_checked_at"] == 123.0
    # unknown columns are ignored rather than raising
    assert db.update_cart_item(item_id, bogus="x")["id"] == item_id
    db.clear_cart()
    assert db.list_cart() == []
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'add_cart_item'`

- [ ] **Step 3: Add the schema** — `app/db.py`

Extend the `SCHEMA` string (after the `saved_lists` table) with:

```sql
CREATE TABLE IF NOT EXISTS cart_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_key      TEXT NOT NULL UNIQUE,
    card_key          TEXT NOT NULL,
    card_name         TEXT NOT NULL,
    set_code          TEXT,
    collector_number  TEXT,
    store_id          TEXT NOT NULL,
    store_name        TEXT NOT NULL,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    printing_hint     TEXT,
    image_url         TEXT,
    condition         TEXT,
    finish            TEXT NOT NULL,
    price_sgd         TEXT NOT NULL,
    currency          TEXT NOT NULL,
    in_stock          INTEGER NOT NULL,
    quantity          INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'ok',
    added_at          REAL NOT NULL,
    offer_fetched_at  TEXT NOT NULL,
    last_checked_at   REAL
);
```

- [ ] **Step 4: Add the methods** — `app/db.py`

Add module-level constants near `CACHE_VERSION`:

```python
CART_FIELDS = (
    "id", "identity_key", "card_key", "card_name", "set_code",
    "collector_number", "store_id", "store_name", "title", "url",
    "printing_hint", "image_url", "condition", "finish", "price_sgd",
    "currency", "in_stock", "quantity", "status", "added_at",
    "offer_fetched_at", "last_checked_at",
)

CART_UPDATABLE = {
    "price_sgd", "currency", "condition", "finish", "in_stock",
    "offer_fetched_at", "last_checked_at", "status",
}


def _cart_row(row) -> dict:
    return {field: row[field] for field in CART_FIELDS}
```

Add these methods to `Database`, before `close`:

```python
    def add_cart_item(self, item: dict) -> tuple[int, bool]:
        existing = self._conn.execute(
            "SELECT id FROM cart_items WHERE identity_key=?",
            (item["identity_key"],),
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False
        cur = self._conn.execute(
            "INSERT INTO cart_items (identity_key, card_key, card_name, set_code, "
            "collector_number, store_id, store_name, title, url, printing_hint, "
            "image_url, condition, finish, price_sgd, currency, in_stock, quantity, "
            "status, added_at, offer_fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["identity_key"], item["card_key"], item["card_name"],
                item.get("set_code"), item.get("collector_number"),
                item["store_id"], item["store_name"], item["title"],
                item["url"], item.get("printing_hint"), item.get("image_url"),
                item.get("condition"), item["finish"], item["price_sgd"],
                item["currency"], int(item["in_stock"]), int(item["quantity"]),
                item.get("status", "ok"), time.time(), item["offer_fetched_at"],
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid), True

    def list_cart(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM cart_items ORDER BY added_at ASC, id ASC"
        ).fetchall()
        return [_cart_row(r) for r in rows]

    def get_cart_item(self, item_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM cart_items WHERE id=?", (item_id,)
        ).fetchone()
        return _cart_row(row) if row is not None else None

    def update_cart_quantity(self, item_id: int, quantity: int) -> dict | None:
        quantity = max(1, int(quantity))
        cur = self._conn.execute(
            "UPDATE cart_items SET quantity=? WHERE id=?", (quantity, item_id)
        )
        self._conn.commit()
        return self.get_cart_item(item_id) if cur.rowcount else None

    def update_cart_item(self, item_id: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in CART_UPDATABLE}
        if not allowed:
            return self.get_cart_item(item_id)
        assignments = ", ".join(f"{k}=?" for k in allowed)
        cur = self._conn.execute(
            f"UPDATE cart_items SET {assignments} WHERE id=?",
            (*allowed.values(), item_id),
        )
        self._conn.commit()
        return self.get_cart_item(item_id) if cur.rowcount else None

    def remove_cart_item(self, item_id: int) -> None:
        self._conn.execute("DELETE FROM cart_items WHERE id=?", (item_id,))
        self._conn.commit()

    def clear_cart(self) -> None:
        self._conn.execute("DELETE FROM cart_items")
        self._conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: persist cart items in sqlite"
```

---

### Task 3: Cart CRUD API

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `app.cart.line_from_offer`; `Database.add_cart_item`, `list_cart`, `update_cart_quantity`, `remove_cart_item`, `clear_cart`.
- Produces HTTP routes: `GET /api/cart`, `POST /api/cart`, `PATCH /api/cart/{item_id}`, `DELETE /api/cart/{item_id}`, `DELETE /api/cart`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`

```python
CART_PAYLOAD = {
    "card": {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
             "collector_number": "409", "quantity": 2},
    "offer": {"store_id": "greyogre", "store_name": "Grey Ogre Games",
              "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
              "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
              "finish": "nonfoil", "in_stock": True, "printing_hint": "ltc:409",
              "fetched_at": "2026-09-20T00:00:00+00:00"},
}


def test_cart_roundtrip(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/cart", json=CART_PAYLOAD).json()
    assert created["created"] is True

    again = client.post("/api/cart", json=CART_PAYLOAD).json()
    assert again["created"] is False
    assert again["id"] == created["id"]

    cart = client.get("/api/cart").json()
    assert cart["count"] == 1
    item = cart["items"][0]
    assert item["quantity"] == 2
    assert item["price_sgd"] == "4.50"
    assert item["card_key"] == "ltc:409"

    patched = client.patch(f"/api/cart/{created['id']}", json={"quantity": 3}).json()
    assert patched["quantity"] == 3
    assert client.patch("/api/cart/9999", json={"quantity": 3}).status_code == 404

    client.delete(f"/api/cart/{created['id']}")
    assert client.get("/api/cart").json()["count"] == 0


def test_cart_clear(tmp_path):
    client = _client(tmp_path)
    client.post("/api/cart", json=CART_PAYLOAD)
    other = dict(CART_PAYLOAD)
    other["card"] = {**CART_PAYLOAD["card"], "key": "war:123", "name": "Shock"}
    other["offer"] = {**CART_PAYLOAD["offer"], "title": "Shock [WAR]",
                      "url": "https://example.test/p/shock"}
    client.post("/api/cart", json=other)
    assert client.get("/api/cart").json()["count"] == 2
    client.delete("/api/cart")
    assert client.get("/api/cart").json()["count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `POST /api/cart` returns 404/405.

- [ ] **Step 3: Add request models and routes** — `app/main.py`

At the top, add to the existing imports:

```python
from app.cart import line_from_offer
```

Add these pydantic models next to `ListRequest`:

```python
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
```

Add these routes inside `create_app`, after the saved-list routes:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (all existing tests plus the two new ones)

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: cart crud api"
```

---

### Task 4: Cart refresh API

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Database.list_cart`, `update_cart_item`; `CardRequest`; `build_adapters`, `_make_http`, `registry` (module-level, monkeypatchable as in `test_run_stream_emits_done`).
- Produces HTTP routes: `PATCH /api/cart/{item_id}/refresh`, `POST /api/cart/refresh` returning `{"run_id", "card_count", "store_ids", "skipped_store_ids"}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`

```python
def test_cart_item_refresh_keeps_price_when_unavailable(tmp_path):
    client = _client(tmp_path)
    item_id = client.post("/api/cart", json=CART_PAYLOAD).json()["id"]

    gone = client.patch(
        f"/api/cart/{item_id}/refresh",
        json={"status": "unavailable", "last_checked_at": 123.0},
    ).json()
    assert gone["status"] == "unavailable"
    assert gone["price_sgd"] == "4.50"  # last-known price retained
    assert gone["last_checked_at"] == 123.0

    fresh = client.patch(
        f"/api/cart/{item_id}/refresh",
        json={"status": "ok", "price_sgd": "5.00", "condition": "LP",
              "finish": "foil", "in_stock": False,
              "offer_fetched_at": "2026-09-21T00:00:00+00:00",
              "last_checked_at": 456.0},
    ).json()
    assert fresh["price_sgd"] == "5.00"
    assert fresh["condition"] == "LP"
    assert fresh["finish"] == "foil"
    assert fresh["in_stock"] == 0


def test_cart_refresh_builds_run(tmp_path, monkeypatch):
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

    assert client.post("/api/cart/refresh").status_code == 400  # empty cart

    client.post("/api/cart", json=CART_PAYLOAD)
    with client:
        data = client.post("/api/cart/refresh").json()
        assert data["card_count"] == 1
        assert data["store_ids"] == ["greyogre"]
        assert data["skipped_store_ids"] == []
        with client.stream("GET", f"/api/runs/{data['run_id']}/stream") as response:
            body = "".join(response.iter_text())
    assert '"type": "done"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `POST /api/cart/refresh` returns 404/405.

- [ ] **Step 3: Add models and routes** — `app/main.py`

Add imports:

```python
from app.adapters.base import CardRequest
```

Add these models next to `QuantityRequest`:

```python
class RefreshItemRequest(BaseModel):
    status: str
    price_sgd: str | None = None
    condition: str | None = None
    finish: str | None = None
    in_stock: bool | None = None
    offer_fetched_at: str | None = None
    last_checked_at: float | None = None
```

Add these routes inside `create_app`, after the cart CRUD routes:

```python
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
        item = db.update_cart_item(item_id, **fields)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.post("/api/cart/refresh")
    def refresh_cart():
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: cart price re-check api"
```

---

### Task 5: Satchel drawer shell (read-only)

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `GET /api/cart` (`{items, count}`).
- Produces: DOM ids `cart-button`, `cart-count`, `cart-drawer`, `cart-close`, `cart-alerts`, `cart-body`, `cart-total`, `cart-excluded`, `cart-refresh`, `cart-clear`, `cart-live`; JS functions `loadCart()`, `renderCart()`, `renderCartBadge()`, `cartIdentity(item)`, `cartHasOffer(offer)`, `openCart()`, `closeCart()`; sprite symbols `i-satchel`, `i-plus`, `i-minus`, `i-cart`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_web.py`

```python
def test_cart_drawer_present(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    html = client.get("/").text
    assert 'id="cart-button"' in html
    assert 'id="cart-drawer"' in html
    assert 'id="cart-body"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web.py -v`
Expected: FAIL — `id="cart-button"` not in the served HTML.

- [ ] **Step 3: Add sprite symbols and markup** — `web/index.html`

Inside the `<svg class="sprite">` block, add:

```html
    <symbol id="i-satchel" viewBox="0 0 24 24"><path d="M5 9h14l-1.2 11a1 1 0 0 1-1 1H7.2a1 1 0 0 1-1-1z" /><path d="M9 9V6.5a3 3 0 0 1 6 0V9" /></symbol>
    <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></symbol>
    <symbol id="i-minus" viewBox="0 0 24 24"><path d="M5 12h14" /></symbol>
    <symbol id="i-cart" viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.4" /><circle cx="18" cy="20" r="1.4" /><path d="M2 3h3l2.6 12.4a1 1 0 0 0 1 .8h8.8a1 1 0 0 0 1-.8L21 7H6" /></symbol>
```

Replace the `.topbar__status` block with a cart button plus the status pill:

```html
    <div class="topbar__status">
      <button id="cart-button" class="cart-btn" type="button" aria-haspopup="dialog" aria-controls="cart-drawer" aria-label="Open the satchel">
        <svg class="ico"><use href="#i-satchel" /></svg>
        <span class="cart-btn__label">Satchel</span>
        <span id="cart-count" class="cart-btn__badge" hidden>0</span>
      </button>
      <span id="status" class="status" data-state="idle">
        <span class="status__pulse" aria-hidden="true"></span>
        <span id="status-text">Ready</span>
      </span>
    </div>
```

Add the drawer just before `<noscript>`:

```html
  <dialog id="cart-drawer" class="satchel" aria-labelledby="cart-title">
    <header class="satchel__head">
      <div>
        <p class="eyebrow">The Satchel</p>
        <h2 id="cart-title" class="satchel__title">Saved offers</h2>
      </div>
      <button id="cart-close" class="btn btn--quiet btn--sm" type="button" aria-label="Close the satchel">
        <svg class="ico"><use href="#i-x" /></svg>
      </button>
    </header>
    <div id="cart-alerts" class="satchel__alerts" hidden></div>
    <div id="cart-body" class="satchel__body"></div>
    <footer class="satchel__foot">
      <div class="satchel__total">
        <span class="satchel__total-label">Grand total</span>
        <span id="cart-total" class="satchel__total-value">S$0.00</span>
        <span id="cart-excluded" class="satchel__excluded" hidden></span>
      </div>
      <div class="satchel__actions">
        <button id="cart-refresh" class="btn btn--quiet btn--sm" type="button">
          <svg class="ico"><use href="#i-refresh" /></svg> Re-check prices
        </button>
        <button id="cart-clear" class="btn btn--quiet btn--sm" type="button">
          <svg class="ico"><use href="#i-trash" /></svg> Clear
        </button>
      </div>
    </footer>
    <div id="cart-live" class="sr-only" aria-live="polite"></div>
  </dialog>
```

- [ ] **Step 4: Add cart state, handles, and rendering** — `web/app.js`

Add to the `els` object (after `toasts`):

```js
  cartButton: $("cart-button"),
  cartCount: $("cart-count"),
  cartDrawer: $("cart-drawer"),
  cartClose: $("cart-close"),
  cartAlerts: $("cart-alerts"),
  cartBody: $("cart-body"),
  cartTotal: $("cart-total"),
  cartExcluded: $("cart-excluded"),
  cartRefresh: $("cart-refresh"),
  cartClear: $("cart-clear"),
  cartLive: $("cart-live"),
```

Add `cart: { items: [] },` to the `state` object.

Add a new section before `/* ── wiring ── */`:

```js
/* ── the satchel ────────────────────────────────────────────── */
function cartIdentity(item) {
  return [item.store_id, item.title, item.condition, item.finish, item.url].join("\u0001");
}

function cartHasOffer(offer) {
  const key = [offer.store_id, offer.title, offer.condition, offer.finish, offer.url].join("\u0001");
  return state.cart.items.some((item) => cartIdentity(item) === key);
}

function cartLineTotal(item) {
  return Number(item.price_sgd) * Number(item.quantity);
}

function cartCountable(item) {
  return item.status === "ok" && Boolean(item.in_stock);
}

function cartGroups() {
  const groups = new Map();
  for (const item of state.cart.items) {
    if (!groups.has(item.store_id)) {
      groups.set(item.store_id, { store_id: item.store_id, store_name: item.store_name, items: [] });
    }
    groups.get(item.store_id).items.push(item);
  }
  return [...groups.values()];
}

function duplicateCartCardKeys() {
  const seen = new Set();
  const dupes = new Set();
  for (const item of state.cart.items) {
    if (seen.has(item.card_key)) dupes.add(item.card_key);
    seen.add(item.card_key);
  }
  return dupes;
}

function renderCartBadge() {
  const count = state.cart.items.length;
  els.cartCount.textContent = String(count);
  els.cartCount.hidden = count === 0;
}

function cartLineHTML(item, dupes) {
  const countable = cartCountable(item);
  const tags = [
    conditionTag(item.condition),
    finishTag(item.finish),
    countable ? "" : `<span class="tag tag--oos">${item.status === "unavailable" ? "No longer listed" : "Out of stock"}</span>`,
    dupes.has(item.card_key) ? '<span class="tag tag--warn">Duplicate choice</span>' : "",
  ].join("");
  const thumb = isSafeUrl(item.image_url)
    ? `<img class="satchel__thumb" src="${esc(item.image_url)}" alt="" loading="lazy" />`
    : '<span class="satchel__thumb satchel__thumb--none" aria-hidden="true"><svg class="ico"><use href="#i-stack" /></svg></span>';
  const link = isSafeUrl(item.url)
    ? `<a class="satchel__link" href="${esc(item.url)}" target="_blank" rel="noopener" title="Open listing"><svg class="ico"><use href="#i-external" /></svg></a>`
    : "";
  return `
    <li class="satchel__line${countable ? "" : " is-dim"}" data-item-id="${item.id}">
      ${thumb}
      <div class="satchel__line-body">
        <div class="satchel__line-name">${esc(item.card_name)}</div>
        <div class="satchel__line-sub">${item.printing_hint ? esc(item.printing_hint) : "name match"}${tags ? " " + tags : ""}</div>
      </div>
      <div class="satchel__qty">
        <button class="satchel__step" type="button" data-act="dec" aria-label="Decrease quantity of ${esc(item.card_name)}"><svg class="ico"><use href="#i-minus" /></svg></button>
        <span class="satchel__qty-num">${item.quantity}</span>
        <button class="satchel__step" type="button" data-act="inc" aria-label="Increase quantity of ${esc(item.card_name)}"><svg class="ico"><use href="#i-plus" /></svg></button>
      </div>
      <div class="satchel__line-price">${money(cartLineTotal(item))}</div>
      ${link}
      <button class="satchel__remove" type="button" data-act="remove" aria-label="Remove ${esc(item.card_name)} from the satchel"><svg class="ico"><use href="#i-trash" /></svg></button>
    </li>`;
}

function renderCart() {
  renderCartBadge();
  const items = state.cart.items;
  if (!items.length) {
    els.cartBody.innerHTML = '<div class="satchel__empty"><svg class="ico"><use href="#i-satchel" /></svg><p>Nothing in the satchel yet. Add offers from your hunt results.</p></div>';
    els.cartAlerts.hidden = true;
    els.cartTotal.textContent = money(0);
    els.cartExcluded.hidden = true;
    return;
  }
  const dupes = duplicateCartCardKeys();
  let total = 0;
  let excluded = 0;
  els.cartBody.innerHTML = cartGroups().map((group) => {
    let subtotal = 0;
    const lines = group.items.map((item) => {
      if (cartCountable(item)) {
        subtotal += cartLineTotal(item);
        total += cartLineTotal(item);
      } else {
        excluded += 1;
      }
      return cartLineHTML(item, dupes);
    }).join("");
    return `
      <section class="satchel__store">
        <header class="satchel__store-head">
          <span class="satchel__store-dot" style="--store-color:${esc(storeColor(group.store_id))}" aria-hidden="true"></span>
          <span class="satchel__store-name">${esc(group.store_name)}</span>
          <span class="satchel__store-sub">${money(subtotal)}</span>
        </header>
        <ul class="satchel__lines">${lines}</ul>
      </section>`;
  }).join("");
  els.cartTotal.textContent = money(total);
  els.cartExcluded.hidden = excluded === 0;
  els.cartExcluded.textContent = excluded
    ? `${excluded} line${excluded === 1 ? "" : "s"} excluded (out of stock or unavailable)`
    : "";
  const dupCount = dupes.size;
  const alerts = [
    dupCount ? `<span class="satchel__alert"><svg class="ico"><use href="#i-alert" /></svg> ${dupCount} card${dupCount === 1 ? "" : "s"} appear twice — remove one before buying.</span>` : "",
    excluded ? `<span class="satchel__alert"><svg class="ico"><use href="#i-alert" /></svg> ${excluded} line${excluded === 1 ? "" : "s"} can’t be bought as-is.</span>` : "",
  ].filter(Boolean);
  els.cartAlerts.hidden = alerts.length === 0;
  els.cartAlerts.innerHTML = alerts.join("");
}

async function loadCart() {
  try {
    const data = await (await fetch("/api/cart")).json();
    state.cart.items = data.items || [];
  } catch {
    state.cart.items = [];
  }
  renderCart();
}

function openCart() {
  if (!els.cartDrawer.open) els.cartDrawer.showModal();
  loadCart();
}

function closeCart() {
  if (els.cartDrawer.open) els.cartDrawer.close();
}

els.cartButton.addEventListener("click", openCart);
els.cartClose.addEventListener("click", closeCart);
els.cartDrawer.addEventListener("click", (e) => {
  if (e.target === els.cartDrawer) closeCart(); // backdrop click
});
```

At the bottom, in the `/* ── boot ── */` block, add `loadCart();`.

- [ ] **Step 5: Add styles** — append to `web/styles.css`

```css
/* ── the satchel ────────────────────────────────────────────── */
.topbar__status { display: flex; align-items: center; gap: 12px; }

.cart-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--panel);
  color: var(--gold-2);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, transform 0.15s;
}
.cart-btn:hover { background: var(--panel-2); border-color: var(--gold); transform: translateY(-1px); }
.cart-btn__label { font-size: 0.82rem; letter-spacing: 0.04em; }
.cart-btn__badge {
  min-width: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--gold);
  color: #1a1206;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 600;
  text-align: center;
}

.satchel {
  width: min(460px, 94vw);
  max-height: 92vh;
  margin: 0 0 0 auto;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius) 0 0 var(--radius);
  background: linear-gradient(180deg, var(--panel-2), var(--bg-2));
  color: var(--ink);
  box-shadow: var(--shadow);
}
.satchel::backdrop { background: rgba(4, 6, 10, 0.68); backdrop-filter: blur(3px); }
.satchel[open] { display: flex; flex-direction: column; animation: satchel-in 0.28s cubic-bezier(0.22, 1, 0.36, 1); }
@keyframes satchel-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }

.satchel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--line);
}
.satchel__title { font-family: var(--font-display); font-size: 1.15rem; }
.satchel__alerts { display: flex; flex-direction: column; gap: 6px; padding: 12px 20px 0; }
.satchel__alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(224, 178, 95, 0.35);
  border-radius: var(--radius-sm);
  background: rgba(224, 178, 95, 0.08);
  color: var(--warn);
  font-size: 0.82rem;
}
.satchel__body { flex: 1; overflow-y: auto; padding: 14px 20px 20px; }
.satchel__empty { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 48px 12px; color: var(--muted); text-align: center; }
.satchel__empty .ico { width: 40px; height: 40px; color: var(--gold-dim); }

.satchel__store { margin-bottom: 18px; }
.satchel__store-head { display: flex; align-items: center; gap: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
.satchel__store-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--store-color, var(--gold)); }
.satchel__store-name { font-weight: 600; }
.satchel__store-sub { margin-left: auto; font-family: var(--font-mono); color: var(--gold-2); }
.satchel__lines { display: flex; flex-direction: column; }
.satchel__line { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.satchel__line.is-dim { opacity: 0.55; }
.satchel__thumb { width: 34px; height: 48px; border-radius: 4px; object-fit: cover; background: var(--raised); }
.satchel__thumb--none { display: grid; place-items: center; color: var(--faint); }
.satchel__line-body { flex: 1; min-width: 0; }
.satchel__line-name { font-weight: 600; }
.satchel__line-sub { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; color: var(--muted); font-size: 0.78rem; }
.satchel__qty { display: flex; align-items: center; gap: 4px; font-family: var(--font-mono); }
.satchel__step { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid var(--line-strong); border-radius: 6px; background: transparent; color: var(--ink-2); cursor: pointer; }
.satchel__step:hover { border-color: var(--gold); color: var(--gold-2); }
.satchel__qty-num { min-width: 18px; text-align: center; }
.satchel__line-price { min-width: 64px; text-align: right; font-family: var(--font-mono); }
.satchel__link, .satchel__remove { display: grid; place-items: center; width: 28px; height: 28px; border: none; background: transparent; color: var(--muted); cursor: pointer; }
.satchel__link:hover, .satchel__remove:hover { color: var(--gold-2); }

.satchel__foot { padding: 16px 20px; border-top: 1px solid var(--line-strong); background: rgba(0, 0, 0, 0.18); }
.satchel__total { display: flex; align-items: baseline; gap: 10px; }
.satchel__total-label { color: var(--muted); font-size: 0.8rem; letter-spacing: 0.05em; text-transform: uppercase; }
.satchel__total-value { margin-left: auto; font-family: var(--font-mono); font-size: 1.5rem; color: var(--gold-2); }
.satchel__excluded { flex-basis: 100%; color: var(--muted); font-size: 0.78rem; }
.satchel__actions { display: flex; gap: 8px; margin-top: 12px; }

.tag--warn { border-color: rgba(224, 178, 95, 0.4); color: var(--warn); }
```

- [ ] **Step 6: Run test and the suite**

Run: `uv run pytest tests/test_web.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Manual verification**

Start `uv run uvicorn app.main:app --reload`, open <http://127.0.0.1:8000>, and confirm the top bar shows a "Satchel" button with no badge; clicking it opens the drawer showing the empty state. (If no offers are saved yet, `GET /api/cart` returns `{"items": [], "count": 0}`.)

- [ ] **Step 8: Commit**

```bash
git add web/index.html web/app.js web/styles.css tests/test_web.py
git commit -m "feat: satchel drawer shell"
```

---

### Task 6: Add-to-cart from results

**Files:**
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `POST /api/cart` body `{card, offer}`; existing `getEntry`, `bestOffer`, `findEntry`, `offerIndexKey`, `offerButton`, `render`, `toast`, `cartHasOffer`.
- Produces: `addOfferToCart(entry, offer)`.

- [ ] **Step 1: Add the add button to offer rows** — `web/app.js`

In `offersHTML`, replace the `link` template + row markup so each row carries an add control. Change the `const link = ...` block and the returned template to:

```js
      const link = isSafeUrl(offer.url)
        ? `<a class="offer__link" href="${esc(offer.url)}" target="_blank" rel="noopener" title="Open listing at ${esc(offer.store_name)}"><svg class="ico"><use href="#i-external" /></svg></a>`
        : `<span class="offer__link" aria-disabled="true" title="Unsafe link hidden"><svg class="ico"><use href="#i-external" /></svg></span>`;
      const printing = offer.printing_hint ? ` · ${esc(offer.printing_hint)}` : "";
      const saved = cartHasOffer(offer);
      const addBtn = saved
        ? `<span class="offer__add is-saved" title="In the satchel" aria-hidden="true"><svg class="ico"><use href="#i-check" /></svg></span>`
        : `<button class="offer__add" type="button" data-act="add-offer" aria-label="Add ${esc(offer.title)} from ${esc(offer.store_name)} to the satchel"><svg class="ico"><use href="#i-plus" /></svg></button>`;
      return `
        <div class="offer ${isBest ? "is-best" : ""}" style="--store-color:${esc(color)}" data-offer-key="${esc(offerIndexKey(offer))}">
          <span class="offer__fill" style="--w:${width.toFixed(1)}%"></span>
          <span class="offer__dot" aria-hidden="true"></span>
          <div class="offer__body">
            <div class="offer__store">
              ${isBest ? '<span class="tag tag--exact">Best</span>' : ""}
              <span>${esc(offer.store_name)}</span>
              ${tags}
            </div>
            <div class="offer__meta">${meta}${printing}</div>
          </div>
          <span class="offer__price">${money(offer.price_sgd)}</span>
          <button class="offer__preview" type="button" data-act="preview"
            aria-label="Preview ${esc(offer.title)} from ${esc(offer.store_name)}">
            <svg class="ico"><use href="#i-eye" /></svg>
          </button>
          ${addBtn}
          ${link}
        </div>`;
```

- [ ] **Step 2: Add the "Add best" control to card rows** — `web/app.js`

In `bestHTML`, replace the `return` with:

```js
  const saved = cartHasOffer(offer);
  const addBest = saved
    ? '<span class="best__add is-saved" title="In the satchel" aria-hidden="true"><svg class="ico"><use href="#i-check" /></svg></span>'
    : `<button class="best__add" type="button" data-act="add-best" aria-label="Add the best offer for ${esc(entry.card.name)} to the satchel"><svg class="ico"><use href="#i-plus" /></svg><span>Add best</span></button>`;
  return `
    <div class="best__row">
      <span class="best__price">${money(offer.price_sgd)}</span>
      ${store}
      ${addBest}
    </div>
    <div class="best__tags">${tags}</div>`;
```

- [ ] **Step 3: Implement `addOfferToCart` and the click handlers** — `web/app.js`

Add before `/* ── wiring ── */`:

```js
async function addOfferToCart(entry, offer) {
  const payload = {
    card: {
      key: entry.card.key,
      name: entry.card.name,
      set_code: entry.card.set_code,
      collector_number: entry.card.collector_number,
      quantity: entry.card.quantity,
    },
    offer: {
      store_id: offer.store_id,
      store_name: offer.store_name,
      title: offer.title,
      url: offer.url,
      price_sgd: String(offer.price_sgd),
      currency: offer.currency || "SGD",
      condition: offer.condition,
      finish: offer.finish,
      in_stock: offer.in_stock,
      printing_hint: offer.printing_hint,
      image_url: offer.image_url,
      fetched_at: offer.fetched_at,
    },
  };
  try {
    const res = await fetch("/api/cart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("rejected");
    const data = await res.json();
    toast({
      type: data.created ? "success" : "info",
      title: data.created ? "Added to the satchel" : "Already in the satchel",
      msg: `${entry.card.name} · ${offer.store_name}`,
    });
    await loadCart();
    render();
  } catch {
    toast({ type: "error", title: "Could not add", msg: "The satchel didn’t save that offer." });
  }
}
```

In the existing `els.resultsList` `click` handler, add these branches **after** the `offerButton` block and **before** the `toggle` lookup:

```js
  const addOffer = e.target.closest('[data-act="add-offer"]');
  if (addOffer) {
    const row = addOffer.closest(".offer");
    const found = row && findEntry(row.dataset.offerKey);
    if (found) addOfferToCart(found.entry, found.match.offer);
    return;
  }
  const addBest = e.target.closest('[data-act="add-best"]');
  if (addBest) {
    const card = addBest.closest(".card");
    const entry = card && state.cards.get(card.dataset.key);
    const best = entry && bestOffer(entry);
    if (entry && best) addOfferToCart(entry, best.offer);
    return;
  }
```

- [ ] **Step 4: Add styles** — append to `web/styles.css`

```css
.offer__add, .best__add { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--line-strong); border-radius: 999px; background: transparent; color: var(--gold-2); cursor: pointer; }
.offer__add { display: grid; place-items: center; width: 28px; height: 28px; padding: 0; }
.best__add { padding: 4px 10px; font-size: 0.74rem; }
.offer__add:hover, .best__add:hover { border-color: var(--gold); background: rgba(201, 169, 97, 0.1); }
.offer__add.is-saved, .best__add.is-saved { border-color: transparent; color: var(--good); }
```

- [ ] **Step 5: Manual verification**

Run the app, paste `1 Sol Ring`, search, then: (a) click `+` on an offer row — a toast says "Added to the satchel" and the button becomes a check; (b) click "Add best" on a card — same; (c) reopen the satchel — the line appears under its store with the card's quantity and the correct line total. Click add twice and confirm the second toast says "Already in the satchel".

- [ ] **Step 6: Run the suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add web/app.js web/styles.css
git commit -m "feat: add offers to the satchel from results"
```

---

### Task 7: Cart mutations (quantity, remove, clear)

**Files:**
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `PATCH /api/cart/{id}` `{quantity}`, `DELETE /api/cart/{id}`, `DELETE /api/cart`.
- Produces: `patchQuantity(itemId, quantity)`.

- [ ] **Step 1: Implement mutations** — `web/app.js`

Add before the `/* ── wiring ── */` section, after `closeCart`:

```js
async function patchQuantity(itemId, quantity) {
  const res = await fetch(`/api/cart/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quantity }),
  });
  if (!res.ok) return;
  const updated = await res.json();
  const index = state.cart.items.findIndex((i) => i.id === itemId);
  if (index >= 0) state.cart.items[index] = updated;
  renderCart();
  render();
}

els.cartBody.addEventListener("click", async (e) => {
  const line = e.target.closest(".satchel__line");
  if (!line) return;
  const itemId = Number(line.dataset.itemId);
  const item = state.cart.items.find((i) => i.id === itemId);
  if (!item) return;
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "inc" || act === "dec") {
    const delta = act === "inc" ? 1 : -1;
    await patchQuantity(itemId, Math.max(1, Number(item.quantity) + delta));
  } else if (act === "remove") {
    await fetch(`/api/cart/${itemId}`, { method: "DELETE" });
    await loadCart();
    render();
    toast({ type: "info", title: "Removed", msg: item.card_name });
  }
});

els.cartClear.addEventListener("click", async () => {
  if (!state.cart.items.length) return;
  if (!confirm("Clear the satchel? This removes every saved offer.")) return;
  await fetch("/api/cart", { method: "DELETE" });
  await loadCart();
  render();
  toast({ type: "info", title: "Satchel cleared" });
});
```

- [ ] **Step 2: Manual verification**

With lines in the satchel: click `+`/`−` and confirm the quantity, line total, store subtotal, and grand total all update (and the update survives a page reload). Click the trash icon and confirm the line disappears. Click Clear and confirm the whole drawer empties after confirming the dialog.

- [ ] **Step 3: Run the suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "feat: edit quantities, remove and clear satchel lines"
```

---

### Task 8: Re-check prices

**Files:**
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `POST /api/cart/refresh`, `GET /api/runs/{run_id}/stream` (events `result`/`done`/`error`), `PATCH /api/cart/{id}/refresh`.
- Produces: `refreshCart()`, `reconcileCart(event, stats)`, `pushCartRefresh(line)`, `finishCartRefresh(message, stats)`.

- [ ] **Step 1: Implement the refresh flow** — `web/app.js`

Add before the `/* ── wiring ── */` section:

```js
/* ── re-check prices ────────────────────────────────────────── */
let cartRefreshing = false;

async function refreshCart() {
  if (cartRefreshing || !state.cart.items.length) return;
  cartRefreshing = true;
  els.cartRefresh.disabled = true;
  els.cartRefresh.dataset.busy = "true";

  let data;
  try {
    const res = await fetch("/api/cart/refresh", { method: "POST" });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || "Could not re-check the satchel.");
    }
    data = await res.json();
  } catch (err) {
    finishCartRefresh(err.message);
    return;
  }

  if (data.skipped_store_ids?.length) {
    toast({ type: "info", title: "Some stores skipped", msg: data.skipped_store_ids.join(", ") });
  }

  const stats = { checked: 0, changed: 0, unavailable: 0 };
  const source = new EventSource(`/api/runs/${data.run_id}/stream`);
  source.addEventListener("result", (e) => {
    reconcileCart(JSON.parse(e.data), stats);
  });
  source.addEventListener("done", () => {
    source.close();
    finishCartRefresh(null, stats);
  });
}

function reconcileCart(event, stats) {
  const lines = state.cart.items.filter(
    (i) => i.card_key === event.card.key && i.store_id === event.store_id
  );
  if (!lines.length) return;
  const byIdentity = new Map(
    (event.offers || []).map((m) => [cartIdentity(m.offer), m.offer])
  );
  for (const line of lines) {
    stats.checked += 1;
    const match = byIdentity.get(cartIdentity(line));
    if (match) {
      const oldPrice = Number(line.price_sgd);
      line.price_sgd = String(match.price_sgd);
      line.condition = match.condition;
      line.finish = match.finish;
      line.in_stock = match.in_stock ? 1 : 0;
      line.offer_fetched_at = match.fetched_at || line.offer_fetched_at;
      line.status = "ok";
      line.last_checked_at = Date.now() / 1000;
      if (oldPrice !== Number(match.price_sgd)) stats.changed += 1;
    } else {
      line.status = "unavailable";
      line.last_checked_at = Date.now() / 1000;
      stats.unavailable += 1;
    }
    pushCartRefresh(line);
  }
  renderCart();
}

async function pushCartRefresh(line) {
  try {
    await fetch(`/api/cart/${line.id}/refresh`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: line.status,
        price_sgd: line.price_sgd,
        condition: line.condition,
        finish: line.finish,
        in_stock: Boolean(line.in_stock),
        offer_fetched_at: line.offer_fetched_at,
        last_checked_at: line.last_checked_at,
      }),
    });
  } catch {
    /* best effort — local state is already updated */
  }
}

function finishCartRefresh(message, stats = { checked: 0, changed: 0, unavailable: 0 }) {
  cartRefreshing = false;
  els.cartRefresh.disabled = false;
  els.cartRefresh.dataset.busy = "false";
  if (message) {
    toast({ type: "error", title: "Re-check failed", msg: message });
    return;
  }
  renderCart();
  toast({
    type: "success",
    title: "Satchel re-checked",
    msg: `${stats.checked} line${stats.checked === 1 ? "" : "s"} · ${stats.changed} price change${stats.changed === 1 ? "" : "s"} · ${stats.unavailable} no longer listed`,
  });
}

els.cartRefresh.addEventListener("click", refreshCart);
```

- [ ] **Step 2: Manual verification**

With at least one line in the satchel, click **Re-check prices**. Confirm the button disables while it runs, the lines update, and a summary toast appears. To exercise the unavailable path without waiting on a live store, add an offer, then stop/edit it out (or temporarily point the cart row's store at a store that won't match) and confirm a missing offer yields "No longer listed" and is excluded from the total. Confirm the refreshed state survives a page reload.

- [ ] **Step 3: Run the suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add web/app.js
git commit -m "feat: re-check satchel prices over the run stream"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: updated user-facing docs.

- [ ] **Step 1: Update the README**

In the **Run** section paragraph, append a sentence:

```markdown
Add any offer to **the Satchel** — a persistent basket in the top bar. It groups
your saved offers by store, totals them (SGD), and can re-check their prices and
stock on demand. There is no checkout; the satchel is a saved basket you buy
from manually.
```

In the **Layout** block, add `app/cart.py` after `app/db.py`:

```text
  cart.py            cart domain helpers (identity, grouping, totals)
```

In the **v1 constraints** list, replace this exact existing line:

```markdown
- No price history, no cart/checkout automation, no accounts.
```

with:

```markdown
- No price history, no accounts. A cart (the Satchel) saves offers for manual
  purchase, but there is no checkout or order automation.
```

- [ ] **Step 2: Verify**

Run: `grep -n "Satchel" README.md`
Expected: at least two matches (the Run paragraph and the constraints line).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the satchel cart"
```

---

## Self-Review

**Spec coverage**

- Persistent single cart in SQLite → Task 2 (`cart_items`, unique `identity_key`).
- Save specific offers (identity dedupe, snapshot) → Tasks 1–3.
- Quantity defaulting to list quantity, editable → Task 1 (`line_from_offer`), Task 3 (`PATCH`), Task 7 (stepper).
- Grouped by store with subtotals + grand total → Task 1 (`group_by_store`, `totals`), Task 5 (render).
- Duplicate choices allowed and flagged → Task 1 (`duplicate_card_keys`), Task 5 (alerts + tag).
- Unavailable/out-of-stock marked, kept, excluded from totals → Task 4 (refresh PATCH), Task 5 (rendering), Task 8 (reconcile).
- Manual "Re-check prices" over the run/SSE pipeline with `force_refresh=True` → Task 4, Task 8.
- Add affordances (per-offer `+`, "Add best", in-satchel state) → Task 6.
- Drawer from a top-bar button, native dialog semantics → Task 5.
- Empty-cart `400`, no-enabled-store `409`, skipped stores reported → Task 4.
- Accessibility: labelled controls and the `cart-live` polite region are added in Task 5; `web/styles.css` already has a global `@media (prefers-reduced-motion: reduce)` block that neutralises the drawer's entrance animation, so no extra rule is needed.
- README updated → Task 9.

**Placeholder scan:** every code step contains complete code; no TBD/TODO and no
"similar to Task N" references.

**Type consistency:**

- `identity_key` in `app/cart.py` joins `store_id|title|condition|finish|url`; the front-end `cartIdentity`/`cartHasOffer` join the same fields with `"\u0001"`. Both are internal to their layer, and server↔client exchange uses the raw field list, so the differing separators are safe.
- `update_cart_item` accepts only `CART_UPDATABLE` columns; `refresh_cart_item` sends exactly those keys.
- `line_from_offer` returns `in_stock` as `0/1`; the API `RefreshItemRequest.in_stock` is a bool and the route converts with `1 if ... else 0`.
- `group_by_store`/`totals` and the client's `cartCountable` use the same rule: `status == "ok" && in_stock`.
- JSON field names match between the FastAPI models and the `app.js` payloads (`price_sgd`, `offer_fetched_at`, `last_checked_at`, `card_key`).
