# Satchel Store Checkout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Buy" button to each store section of the Satchel drawer that opens that store's pre-filled Shopify checkout for every purchasable line you have saved from it.

**Architecture:** Every store is Shopify, which exposes an unauthenticated cart permalink: `{base_url}/cart/{variant_id}:{qty},{variant_id}:{qty}` opens a checkout pre-loaded with those variants. The variant ID is already present in the `products/{handle}.js` payload the adapter fetches; we thread it onto `Offer`, into the cached offer, onto the saved cart row, and up to the API. The server computes one permalink per store group (pure, testable helper `checkout_url`); the front end renders it as a plain `<a>` in each store header. No new API endpoint, no token, no JS build step.

**Tech Stack:** Python 3.11+ / FastAPI / SQLite / pytest; plain HTML/CSS/JS front end.

## Global Constraints

- SGD only; no currency conversion.
- Links only — **no order placement, no payment automation**. The permalink opens the store's own checkout; the user still pays there.
- Use only the public, unauthenticated Shopify cart permalink. Do **not** add Storefront API / Admin API tokens or calls.
- A permalink may include only lines where `status == "ok"`, `in_stock` is truthy, and `variant_id` is a string of digits. Everything else is skipped.
- Front end stays plain HTML/CSS/JS: no framework, no build step.
- `CACHE_VERSION` is bumped whenever the cached `Offer` shape changes.
- The existing `CREATE TABLE IF NOT EXISTS` schema does not alter an existing table — any new `cart_items` column needs an `ALTER TABLE` migration that preserves existing rows.
- Match existing naming/style; keep helpers pure and unit-tested.

---

### Task 1: Capture the Shopify variant ID on offers

**Files:**
- Modify: `app/adapters/base.py` (Offer dataclass, `to_dict`)
- Modify: `app/adapters/shopify.py` (`_product_to_offers`)
- Modify: `app/orchestrator.py` (`_offer_from_dict`)
- Modify: `app/db.py` (`CACHE_VERSION`)
- Test: `tests/test_models.py`, `tests/test_shopify_adapter.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Offer.variant_id: str | None` (default `None`), serialized as `"variant_id"` by `Offer.to_dict()`. Shopify offers carry the variant ID as a string (e.g. `"41984399442000"`); non-Shopify offers leave it `None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_models.py`, replace the body of `test_offer_to_dict_serializes_decimal_and_datetime` so the constructed `Offer` also passes `variant_id` and the serialized dict is asserted:

```python
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
        variant_id="41984399442000",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    data = offer.to_dict()
    assert data["price_sgd"] == "4.50"
    assert isinstance(data["price_sgd"], str)
    assert data["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert data["finish"] == "nonfoil"
    assert data["variant_id"] == "41984399442000"
```

In `tests/test_shopify_adapter.py`, inside `test_search_maps_real_variants_to_offers`, add after the `nm.url` assertion block:

```python
    # the .js fixture's "Near Mint" variant is id 41984399442000
    assert nm.variant_id == "41984399442000"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py::test_offer_to_dict_serializes_decimal_and_datetime tests/test_shopify_adapter.py::test_search_maps_real_variants_to_offers -v`
Expected: FAIL — `Offer.__init__() got an unexpected keyword argument 'variant_id'` and `nm.variant_id` raising `AttributeError`.

- [ ] **Step 3: Add the field to the Offer model**

In `app/adapters/base.py`, add `variant_id` to the dataclass between `image_url` and `fetched_at`:

```python
    printing_hint: str | None = None
    image_url: str | None = None
    variant_id: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

In the same file, add it to `to_dict` after `"image_url"`:

```python
            "printing_hint": self.printing_hint,
            "image_url": self.image_url,
            "variant_id": self.variant_id,
            "fetched_at": self.fetched_at.isoformat(),
```

- [ ] **Step 4: Populate it in the Shopify adapter**

In `app/adapters/shopify.py`, inside `_product_to_offers`, add `variant_id` to the `Offer(...)` construction (after `image_url`):

```python
            offers.append(Offer(
                store_id=self.store_id,
                store_name=self.store_name,
                title=title,
                price_sgd=price,
                currency="SGD",
                condition=parse_condition(combined),
                finish=finish,
                in_stock=bool(variant.get("available", False)),
                url=absolute,
                printing_hint=printing_hint,
                image_url=image_url,
                variant_id=(
                    str(variant["id"]) if variant.get("id") is not None else None
                ),
            ))
```

- [ ] **Step 5: Carry it through the cache round-trip**

In `app/orchestrator.py`, add `variant_id` to `_offer_from_dict`'s kwargs, after `image_url`:

```python
        printing_hint=data.get("printing_hint"),
        image_url=data.get("image_url"),
        variant_id=data.get("variant_id"),
    )
```

In `app/db.py`, bump the cache version so rows written before this change are a miss:

```python
CACHE_VERSION = 3
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_shopify_adapter.py tests/test_orchestrator.py tests/test_db.py -v`
Expected: PASS. (`tests/test_db.py::test_cache_ignores_payloads_from_an_older_schema` still passes because it uses `CACHE_VERSION - 1`.)

- [ ] **Step 7: Commit**

```bash
git add app/adapters/base.py app/adapters/shopify.py app/orchestrator.py app/db.py tests/test_models.py tests/test_shopify_adapter.py
git commit -m "feat: capture Shopify variant id on offers"
```

---

### Task 2: Persist the variant ID on satchel rows

**Files:**
- Modify: `app/db.py` (schema, migration, `CART_FIELDS`, `CART_UPDATABLE`, `add_cart_item`)
- Modify: `app/cart.py` (`line_from_offer`)
- Test: `tests/test_db.py`, `tests/test_cart.py`

**Interfaces:**
- Consumes: `Offer.variant_id` (Task 1), arriving in the add payload as `offer["variant_id"]`.
- Produces: `cart_items.variant_id TEXT` (nullable); `db.list_cart()` / `db.get_cart_item()` rows include `"variant_id"`; `line_from_offer(card, offer)` sets `"variant_id"` from `offer.get("variant_id")`; `variant_id` is updatable via `db.update_cart_item`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cart.py`, add a `variant_id` assertion to `test_line_from_offer_copies_card_and_offer_fields` and two new tests:

```python
def test_line_from_offer_copies_variant_id():
    row = line_from_offer(CARD, _offer(variant_id="41984399442000"))
    assert row["variant_id"] == "41984399442000"


def test_line_from_offer_variant_id_defaults_to_none():
    assert line_from_offer(CARD, _offer())["variant_id"] is None
```

In `tests/test_db.py`, add `"variant_id": "41984399442000"` to the dict passed as the second argument in the `_row` helper:

```python
def _row(**over):
    row = line_from_offer(
        {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
         "collector_number": "409", "quantity": 2},
        {"store_id": "greyogre", "store_name": "Grey Ogre Games",
         "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
         "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
         "finish": "nonfoil", "in_stock": True,
         "variant_id": "41984399442000",
         "fetched_at": "2026-09-20T00:00:00+00:00"},
    )
    row.update(over)
    return row
```

Then add two tests at the end of `tests/test_db.py`:

```python
def test_cart_persists_variant_id(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.add_cart_item(_row())
    assert db.list_cart()[0]["variant_id"] == "41984399442000"
    db.close()


def test_init_schema_adds_variant_id_to_an_older_cart_table(tmp_path):
    db = Database(tmp_path / "t.db")
    db._conn.executescript(
        "CREATE TABLE cart_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "identity_key TEXT NOT NULL UNIQUE);"
    )
    db._conn.execute("INSERT INTO cart_items (identity_key) VALUES ('old')")
    db._conn.commit()

    db.init_schema()

    columns = {r["name"] for r in db._conn.execute("PRAGMA table_info(cart_items)")}
    assert "variant_id" in columns
    # the pre-existing row survives the ALTER
    assert db._conn.execute(
        "SELECT COUNT(*) AS n FROM cart_items"
    ).fetchone()["n"] == 1
    db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cart.py::test_line_from_offer_copies_variant_id tests/test_db.py::test_cart_persists_variant_id tests/test_db.py::test_init_schema_adds_variant_id_to_an_older_cart_table -v`
Expected: FAIL — `KeyError: 'variant_id'`.

- [ ] **Step 3: Add the column and migration to the database**

In `app/db.py`, add `variant_id TEXT,` to the `cart_items` table in `SCHEMA`, after the `url` line:

```sql
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    variant_id        TEXT,
    printing_hint     TEXT,
```

Add a column map near `CACHE_VERSION`:

```python
# Columns added to cart_items after the table first shipped. ``CREATE TABLE IF
# NOT EXISTS`` will not alter an existing table, so init_schema applies these
# with ALTER TABLE, preserving any saved satchel rows.
CART_ADDED_COLUMNS = {"variant_id": "TEXT"}
```

Replace `init_schema` and add the migration helper:

```python
    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._add_missing_cart_columns()
        self._conn.commit()

    def _add_missing_cart_columns(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(cart_items)")
        }
        for name, decl in CART_ADDED_COLUMNS.items():
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE cart_items ADD COLUMN {name} {decl}"
                )
```

Add `"variant_id"` to `CART_FIELDS` after `"url"`:

```python
CART_FIELDS = (
    "id", "identity_key", "card_key", "card_name", "set_code",
    "collector_number", "store_id", "store_name", "title", "url",
    "variant_id", "printing_hint", "image_url", "condition", "finish",
    "price_sgd", "currency", "in_stock", "quantity", "status", "added_at",
    "offer_fetched_at", "last_checked_at",
)
```

Add `"variant_id"` to `CART_UPDATABLE`:

```python
CART_UPDATABLE = {
    "price_sgd", "currency", "condition", "finish", "in_stock",
    "offer_fetched_at", "last_checked_at", "status", "variant_id",
}
```

Update the `add_cart_item` INSERT to include the new column (21 placeholders now):

```python
        cur = self._conn.execute(
            "INSERT INTO cart_items (identity_key, card_key, card_name, set_code, "
            "collector_number, store_id, store_name, title, url, variant_id, "
            "printing_hint, image_url, condition, finish, price_sgd, currency, "
            "in_stock, quantity, status, added_at, offer_fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["identity_key"], item["card_key"], item["card_name"],
                item.get("set_code"), item.get("collector_number"),
                item["store_id"], item["store_name"], item["title"],
                item["url"], item.get("variant_id"), item.get("printing_hint"),
                item.get("image_url"), item.get("condition"), item["finish"],
                item["price_sgd"], item["currency"], int(item["in_stock"]),
                int(item["quantity"]), item.get("status", "ok"), time.time(),
                item["offer_fetched_at"],
            ),
        )
```

- [ ] **Step 4: Copy the variant ID in the cart domain helper**

In `app/cart.py`, add `"variant_id"` to the row returned by `line_from_offer`, after `"url"`:

```python
        "title": offer["title"],
        "url": offer["url"],
        "variant_id": offer.get("variant_id"),
        "printing_hint": offer.get("printing_hint"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py tests/test_cart.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/cart.py tests/test_db.py tests/test_cart.py
git commit -m "feat: persist variant id on satchel rows"
```

---

### Task 3: Accept and refresh the variant ID through the cart API

**Files:**
- Modify: `app/main.py` (`CartOffer`, `RefreshItemRequest`, `refresh_cart_item`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `cart_items.variant_id` (Task 2).
- Produces: `POST /api/cart` accepts `offer.variant_id`; `GET /api/cart` items include `variant_id`; `PATCH /api/cart/{id}/refresh` sets `variant_id` when the request supplies one and leaves the stored value untouched when the field is absent or `null`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, add `"variant_id": "41984399442000"` to the `offer` dict of `CART_PAYLOAD`:

```python
CART_PAYLOAD = {
    "card": {"key": "ltc:409", "name": "Sol Ring", "set_code": "LTC",
             "collector_number": "409", "quantity": 2},
    "offer": {"store_id": "greyogre", "store_name": "Grey Ogre Games",
              "title": "Sol Ring [LTC]", "url": "https://example.test/p/sol-ring",
              "price_sgd": "4.50", "currency": "SGD", "condition": "NM",
              "finish": "nonfoil", "in_stock": True, "printing_hint": "ltc:409",
              "variant_id": "41984399442000",
              "fetched_at": "2026-09-20T00:00:00+00:00"},
}
```

Add an assertion to `test_cart_roundtrip`, next to the existing item assertions:

```python
    assert item["variant_id"] == "41984399442000"
```

Add two tests at the end of the file:

```python
def test_cart_refresh_updates_variant_id_when_present(tmp_path):
    client = _client(tmp_path)
    item_id = client.post("/api/cart", json=CART_PAYLOAD).json()["id"]

    fresh = client.patch(
        f"/api/cart/{item_id}/refresh",
        json={"status": "ok", "price_sgd": "5.00", "condition": "NM",
              "finish": "nonfoil", "in_stock": True,
              "offer_fetched_at": "2026-09-21T00:00:00+00:00",
              "last_checked_at": 456.0, "variant_id": "999"},
    ).json()
    assert fresh["variant_id"] == "999"


def test_cart_refresh_without_variant_id_keeps_stored_one(tmp_path):
    client = _client(tmp_path)
    item_id = client.post("/api/cart", json=CART_PAYLOAD).json()["id"]

    fresh = client.patch(
        f"/api/cart/{item_id}/refresh",
        json={"status": "ok", "price_sgd": "5.00", "condition": "NM",
              "finish": "nonfoil", "in_stock": True,
              "offer_fetched_at": "2026-09-21T00:00:00+00:00",
              "last_checked_at": 456.0},
    ).json()
    assert fresh["variant_id"] == "41984399442000"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "variant_id" -v`
Expected: FAIL — `item["variant_id"]` / `fresh["variant_id"]` raising `KeyError`.

- [ ] **Step 3: Add the field to the API models**

In `app/main.py`, add `variant_id` to `CartOffer` after `url`:

```python
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
```

Add `variant_id` to `RefreshItemRequest` after `finish`:

```python
class RefreshItemRequest(BaseModel):
    status: str
    price_sgd: str | None = None
    condition: str | None = None
    finish: str | None = None
    variant_id: str | None = None
    in_stock: bool | None = None
    offer_fetched_at: str | None = None
    last_checked_at: float | None = None
```

- [ ] **Step 4: Apply the variant ID on a successful refresh**

In `app/main.py`, update the `refresh_cart_item` route so a supplied variant ID is written and an absent one is left untouched:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: accept and refresh satchel variant ids via the API"
```

---

### Task 4: Build one Shopify checkout permalink per store

**Files:**
- Modify: `app/cart.py` (add `checkout_url`)
- Modify: `app/main.py` (`GET /api/cart` response)
- Test: `tests/test_cart.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: cart rows with `status`, `in_stock`, `quantity`, `variant_id`; `StoreConfig.base_url`; existing `group_by_store(items)`.
- Produces: `checkout_url(base_url: str, items: list[dict]) -> str | None` — a `{base_url}/cart/{variant_id}:{qty},...` permalink for the purchasable lines only, or `None`. `GET /api/cart` returns a new `"stores"` list: `[{"store_id", "store_name", "checkout_url" | None}]`, in first-seen store order, alongside the existing `"items"` and `"count"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cart.py`, add `checkout_url` to the import at the top:

```python
from app.cart import (
    checkout_url,
    duplicate_card_keys,
    group_by_store,
    identity_key,
    line_from_offer,
    totals,
)
```

Add these tests at the end of `tests/test_cart.py`:

```python
def test_checkout_url_joins_purchasable_lines_in_order():
    items = [
        _item(id=1, variant_id="111", quantity=2),
        _item(id=2, variant_id="222", quantity=1),
    ]
    assert checkout_url("https://example.test", items) == "https://example.test/cart/111:2,222:1"


def test_checkout_url_skips_excluded_and_unbuyable_lines():
    items = [
        _item(id=1, variant_id="111"),
        _item(id=2, variant_id="222", in_stock=0),
        _item(id=3, variant_id="333", status="unavailable"),
        _item(id=4, variant_id=None),
        _item(id=5, variant_id="not-a-number"),
    ]
    assert checkout_url("https://example.test", items) == "https://example.test/cart/111:2"


def test_checkout_url_returns_none_without_purchasable_lines():
    assert checkout_url("https://example.test", [_item(id=1, variant_id=None)]) is None
    assert checkout_url("", [_item(id=1, variant_id="111")]) is None


def test_checkout_url_clamps_quantity_to_at_least_one():
    assert checkout_url(
        "https://example.test/", [_item(id=1, variant_id="111", quantity=0)]
    ) == "https://example.test/cart/111:1"
```

In `tests/test_api.py`, add a test at the end of the file:

```python
def test_cart_returns_per_store_checkout_url(tmp_path):
    client = _client(tmp_path)
    client.post("/api/cart", json=CART_PAYLOAD)

    data = client.get("/api/cart").json()
    store = next(s for s in data["stores"] if s["store_id"] == "greyogre")
    # config base_url is https://example.test; the card quantity is 2
    assert store["checkout_url"] == "https://example.test/cart/41984399442000:2"


def test_cart_checkout_url_is_none_without_variant_id(tmp_path):
    client = _client(tmp_path)
    payload = {
        "card": CART_PAYLOAD["card"],
        "offer": {**CART_PAYLOAD["offer"], "variant_id": None},
    }
    client.post("/api/cart", json=payload)

    data = client.get("/api/cart").json()
    store = next(s for s in data["stores"] if s["store_id"] == "greyogre")
    assert store["checkout_url"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cart.py -k checkout_url tests/test_api.py -k checkout_url -v`
Expected: FAIL — `ImportError: cannot import name 'checkout_url'` and `KeyError: 'stores'`.

- [ ] **Step 3: Write the pure helper**

In `app/cart.py`, add `import re` at the top with the other imports, and add below the imports:

```python
_VARIANT_ID_RE = re.compile(r"^\d+$")


def checkout_url(base_url: str, items: list[dict]) -> str | None:
    """Shopify cart permalink for one store's purchasable satchel lines.

    Only lines that are ``status == "ok"``, in stock, and carry a numeric
    ``variant_id`` are included; anything else would land in the store's cart
    as a sold-out or unmatched item. Returns ``None`` when nothing qualifies.
    """
    if not base_url:
        return None
    tokens: list[str] = []
    for item in items:
        if item.get("status") != "ok" or not item.get("in_stock"):
            continue
        variant_id = str(item.get("variant_id") or "")
        if not _VARIANT_ID_RE.match(variant_id):
            continue
        quantity = max(1, int(item.get("quantity") or 1))
        tokens.append(f"{variant_id}:{quantity}")
    if not tokens:
        return None
    return f"{base_url.rstrip('/')}/cart/" + ",".join(tokens)
```

- [ ] **Step 4: Return the permalinks from the cart endpoint**

In `app/main.py`, update the import:

```python
from app.cart import checkout_url, group_by_store, line_from_offer
```

Replace the `GET /api/cart` handler:

```python
    @app.get("/api/cart")
    def get_cart():
        items = db.list_cart()
        base_urls = {c.id: c.base_url for c in configs}
        stores = [
            {
                "store_id": group["store_id"],
                "store_name": group["store_name"],
                "checkout_url": checkout_url(
                    base_urls.get(group["store_id"], ""), group["items"]
                ),
            }
            for group in group_by_store(items)
        ]
        return {"items": items, "count": len(items), "stores": stores}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cart.py tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/cart.py app/main.py tests/test_cart.py tests/test_api.py
git commit -m "feat: compute per-store Shopify checkout permalinks"
```

---

### Task 5: Render a Buy button per store in the Satchel

**Files:**
- Modify: `web/app.js` (cart state, `loadCart`, `renderCart`, `addOfferToCart`, `reconcileCart`, `pushCartRefresh`, `patchQuantity`)
- Modify: `web/styles.css` (`.satchel__store-head`, `.satchel__buy`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `GET /api/cart` → `"stores": [{store_id, store_name, checkout_url}]` (Task 4); `offer.variant_id` on streamed offers (Task 1); `POST/PATCH` accepting `variant_id` (Tasks 2–3).
- Produces: each store section header links to the store's checkout (`<a class="satchel__buy">`), or shows a disabled span when there is no purchasable line. Shopping-list additions and re-checks now send `variant_id`.

- [ ] **Step 1: Write the failing test**

In `tests/test_web.py`, add at the end of the file:

```python
def test_satchel_buy_affordance_present(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    assert "satchel__buy" in client.get("/app.js").text
    assert "satchel__buy" in client.get("/styles.css").text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_web.py::test_satchel_buy_affordance_present -v`
Expected: FAIL — `assert 'satchel__buy' in ...` for app.js.

- [ ] **Step 3: Track the server-provided checkout URLs**

In `web/app.js`, extend the cart slice of `state`:

```js
  cart: { items: [], stores: [] },
```

In `loadCart`, capture the new `stores` list:

```js
async function loadCart() {
  try {
    const data = await (await fetch("/api/cart")).json();
    state.cart.items = data.items || [];
    state.cart.stores = data.stores || [];
  } catch {
    toast({ type: "error", title: "Could not load the satchel", msg: "Showing the last known basket." });
  }
  renderCart();
}
```

- [ ] **Step 4: Render the Buy link in each store header**

In `web/app.js`, inside `renderCart`, build a lookup before rendering the groups:

```js
  const checkoutUrls = new Map(
    (state.cart.stores || []).map((store) => [store.store_id, store.checkout_url])
  );
```

Then change the group map so each store header ends with the buy affordance:

```js
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
    const checkoutUrl = checkoutUrls.get(group.store_id);
    const buy = checkoutUrl
      ? `<a class="satchel__buy" href="${esc(checkoutUrl)}" target="_blank" rel="noopener" title="Check out these lines at ${esc(group.store_name)}"><svg class="ico"><use href="#i-cart" /></svg> Buy</a>`
      : `<span class="satchel__buy is-disabled" aria-disabled="true" title="No purchasable variants at ${esc(group.store_name)}"><svg class="ico"><use href="#i-cart" /></svg> Buy</span>`;
    return `
      <section class="satchel__store">
        <header class="satchel__store-head">
          <span class="satchel__store-dot" style="--store-color:${esc(storeColor(group.store_id))}" aria-hidden="true"></span>
          <span class="satchel__store-name">${esc(group.store_name)}</span>
          <span class="satchel__store-sub">${money(subtotal)}</span>
          ${buy}
        </header>
        <ul class="satchel__lines">${lines}</ul>
      </section>`;
  }).join("");
```

- [ ] **Step 5: Send the variant ID when saving and re-checking offers**

In `web/app.js`, in `addOfferToCart`, add `variant_id` to the offer payload after `url`:

```js
    offer: {
      store_id: offer.store_id,
      store_name: offer.store_name,
      title: offer.title,
      url: offer.url,
      variant_id: offer.variant_id ?? null,
      price_sgd: String(offer.price_sgd),
```

In `reconcileCart`, carry a fresh variant ID onto the line when the offer is found, right after `line.status = "ok";`:

```js
      line.status = "ok";
      line.variant_id = match.variant_id ?? line.variant_id;
      line.last_checked_at = Date.now() / 1000;
```

In `pushCartRefresh`, include it in the PATCH body after `finish`:

```js
      body: JSON.stringify({
        status: line.status,
        price_sgd: line.price_sgd,
        condition: line.condition,
        finish: line.finish,
        variant_id: line.variant_id ?? null,
        in_stock: Boolean(line.in_stock),
```

- [ ] **Step 6: Reload the cart after a quantity change so the permalink updates**

In `web/app.js`, replace the body of `patchQuantity` so the server recomputes the store permalinks with the new quantity:

```js
async function patchQuantity(itemId, quantity) {
  const res = await fetch(`/api/cart/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quantity }),
  });
  if (!res.ok) return;
  await loadCart();
  render();
}
```

- [ ] **Step 7: Style the button**

In `web/styles.css`, add `flex-wrap: wrap;` to the existing `.satchel__store-head` rule:

```css
.satchel__store-head { display: flex; align-items: center; gap: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
```

Add the button rules directly after the `.satchel__store-sub` rule:

```css
.satchel__buy { display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; font-size: 0.76rem; font-weight: 600; color: var(--gold-2); background: transparent; border: 1px solid var(--line); border-radius: 999px; text-decoration: none; white-space: nowrap; }
.satchel__buy .ico { width: 13px; height: 13px; }
.satchel__buy:hover { border-color: var(--gold); color: var(--gold); }
.satchel__buy.is-disabled { opacity: 0.4; cursor: default; }
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add web/app.js web/styles.css tests/test_web.py
git commit -m "feat: add a per-store Buy button to the satchel"
```

---

## Manual verification (after Task 5)

Run the app from the repo root: `uv run uvicorn app.main:app --reload`, open <http://127.0.0.1:8000>.

1. Paste `1 Sol Ring`, select Grey Ogre Games, search, add an in-stock NM non-foil offer to the Satchel twice (or a second store too).
2. Open the Satchel. Each store section shows a **Buy** button.
3. Click **Buy**. A new tab opens `https://www.greyogregames.com/cart/<variant_id>:<qty>` and redirects to Grey Ogre's checkout (or its login, preserving the checkout URL) with the line pre-loaded.
4. Change a line's quantity, then click **Buy** again — the permalink reflects the new quantity.
5. Mark every line of a store out of stock / unavailable (or add one with no variant), and confirm its button is disabled with the explanatory title.

## Self-review notes

- **Spec coverage:** variant capture (Task 1), persistence/migration (Task 2), API in/out + refresh (Task 3), permalink helper + endpoint (Task 4), per-store button + wiring + CSS (Task 5). Manual verification covers the end-to-end click.
- **Placeholders:** none — every code step contains the exact change.
- **Type consistency:** `variant_id` is `str | None` everywhere (`Offer`, `CartOffer`, `RefreshItemRequest`, `cart_items.variant_id TEXT`, `checkout_url`); `checkout_url(base_url, items)` and `group_by_store(items)` are used in `app/main.py` exactly as defined in `app/cart.py`.
- **Constraint check:** no tokens, no order placement; only `status == "ok"` + in-stock + numeric-variant lines enter a permalink.
