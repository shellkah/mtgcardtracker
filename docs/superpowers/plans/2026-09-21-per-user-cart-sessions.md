# Per-User Cart Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every anonymous browser its own Satchel by keying cart lines on an HTTP-only session cookie, and purge idle sessions after a configurable TTL.

**Architecture:** A `cart_sessions` table holds one row per opaque cookie token with a sliding `last_seen`. `cart_items` gains `owner_id`, and uniqueness moves from `identity_key` alone to `(owner_id, identity_key)`. A new `app/sessions.py` mints cookies and runs an inline purge at the start of every cart request; `app/main.py` injects the resolved owner into every `/api/cart*` route. The frontend is untouched.

**Tech Stack:** Python 3.11+, FastAPI 0.115, SQLite (stdlib `sqlite3`), Starlette requests/responses, pytest 8.

## Global Constraints

- Anonymous session identity only — no auth, login, or accounts.
- `saved_lists` is **not** touched; it stays global.
- Env var `MTG_CART_TTL_DAYS`, default `30`. Empty, missing, non-numeric, or non-positive values fall back to the default.
- Cookie name `mtg_cart`; attributes `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` = TTL seconds, `Secure` only when the request scheme is `https`.
- `cart_items.owner_id TEXT NOT NULL`; uniqueness enforced by a unique index on `(owner_id, identity_key)`, not on `identity_key` alone.
- Legacy ownerless `cart_items` rows are discarded during migration.
- `/api/cart*` response shapes are unchanged; do not edit anything under `web/`.
- The token is generated with `secrets.token_urlsafe(32)`.

---

## File Structure

- `app/db.py` (modify): schema for `cart_sessions` and the rebuilt `cart_items`, the migration, session persistence, and owner-scoped cart CRUD.
- `app/sessions.py` (create): cookie attributes, token generation, TTL config, `resolve_owner`, and purge orchestration.
- `app/main.py` (modify): `cart_owner` dependency and owner-scoped cart routes.
- `tests/test_db.py` (modify): update existing cart tests for `owner_id`; add migration, session, isolation, and purge tests.
- `tests/test_sessions.py` (create): unit tests for `app/sessions.py`.
- `tests/test_api.py` (modify): add per-browser isolation, cookie, and TTL-expiry tests.
- `.env.example`, `compose.yaml`, `README.md` (modify): document `MTG_CART_TTL_DAYS`.

---

### Task 1: Owner-scoped cart storage and sessions

Changing the `cart_items` schema to `owner_id NOT NULL` forces the CRUD methods to change in the same task, or the existing `test_db.py` cart tests fail on a NOT NULL violation. Schema, migration, session rows, and owner-scoped CRUD therefore land together.

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (all on `Database`):
  - `create_session(owner_id: str) -> None`
  - `touch_session(owner_id: str) -> bool` — `True` if the session row exists
  - `purge_expired_sessions(cutoff: float) -> None`
  - `add_cart_item(owner_id: str, item: dict) -> tuple[int, bool]`
  - `list_cart(owner_id: str) -> list[dict]`
  - `get_cart_item(owner_id: str, item_id: int) -> dict | None`
  - `update_cart_quantity(owner_id: str, item_id: int, quantity: int) -> dict | None`
  - `update_cart_item(owner_id: str, item_id: int, **fields) -> dict | None`
  - `remove_cart_item(owner_id: str, item_id: int) -> bool` — `True` if a row was deleted
  - `clear_cart(owner_id: str) -> None`
  - Table `cart_sessions(id TEXT PRIMARY KEY, created_at REAL NOT NULL, last_seen REAL NOT NULL)`
  - Unique index `ux_cart_items_owner_identity` on `cart_items(owner_id, identity_key)`

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, replace `test_init_schema_adds_variant_id_to_an_older_cart_table` with the migration-rebuild test, and add the session/isolation tests below. Keep the existing `_row()` helper.

```python
def test_init_schema_rebuilds_legacy_cart_table(tmp_path):
    db = Database(tmp_path / "t.db")
    db._conn.executescript(
        "CREATE TABLE cart_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "identity_key TEXT NOT NULL UNIQUE, "
        "variant_id TEXT);"
    )
    db._conn.execute("INSERT INTO cart_items (identity_key) VALUES ('old')")
    db._conn.commit()

    db.init_schema()

    columns = {r["name"] for r in db._conn.execute("PRAGMA table_info(cart_items)")}
    assert "owner_id" in columns
    # ownerless legacy rows are discarded, not adopted
    assert db._conn.execute(
        "SELECT COUNT(*) AS n FROM cart_items"
    ).fetchone()["n"] == 0
    db.close()


def test_session_create_touch_and_purge(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()

    db.create_session("owner-a")
    assert db.touch_session("owner-a") is True
    assert db.touch_session("owner-missing") is False

    db.create_session("owner-b")
    db._conn.execute("UPDATE cart_sessions SET last_seen=1.0 WHERE id='owner-b'")
    db._conn.commit()

    db.purge_expired_sessions(cutoff=time.time() - 60)
    remaining = {r["id"] for r in db._conn.execute("SELECT id FROM cart_sessions")}
    assert remaining == {"owner-a"}
    db.close()


def test_cart_is_isolated_per_owner(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.add_cart_item("owner-a", _row())
    assert len(db.list_cart("owner-a")) == 1
    assert db.list_cart("owner-b") == []
    # the same offer may live in two carts...
    assert db.add_cart_item("owner-b", _row())[1] is True
    # ...but is deduped within one cart
    assert db.add_cart_item("owner-a", _row())[1] is False
    db.close()


def test_owner_scoped_mutations_miss_other_owners(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, _ = db.add_cart_item("owner-a", _row())
    assert db.update_cart_quantity("owner-b", item_id, 5) is None
    assert db.update_cart_item("owner-b", item_id, status="unavailable") is None
    assert db.remove_cart_item("owner-b", item_id) is False
    db.clear_cart("owner-b")
    assert len(db.list_cart("owner-a")) == 1
    # the owner can still act on its own line
    assert db.update_cart_quantity("owner-a", item_id, 5)["quantity"] == 5
    assert db.remove_cart_item("owner-a", item_id) is True
    db.close()


def test_purge_removes_orphaned_cart_items(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.add_cart_item("ghost", _row())
    db.purge_expired_sessions(cutoff=time.time() - 60)
    assert db._conn.execute(
        "SELECT COUNT(*) AS n FROM cart_items"
    ).fetchone()["n"] == 0
    db.close()
```

Also update the four existing cart tests to pass an owner id (`"owner-a"`):

- `test_cart_add_dedupes_identical_offers`: `db.add_cart_item("owner-a", _row())` twice and `db.list_cart("owner-a")`.
- `test_cart_quantity_update_and_remove`: `db.add_cart_item("owner-a", _row())`, `db.update_cart_quantity("owner-a", item_id, 5)`, `db.update_cart_quantity("owner-a", 9999, 2) is None`, `db.remove_cart_item("owner-a", item_id)`.
- `test_cart_refresh_update_and_clear`: `db.add_cart_item("owner-a", _row())`, `db.update_cart_item("owner-a", item_id, ...)`, `db.clear_cart("owner-a")`, `db.list_cart("owner-a")`.
- `test_cart_persists_variant_id`: `db.add_cart_item("owner-a", _row())`, `db.list_cart("owner-a")[0]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — `add_cart_item()` takes 1 positional argument but 2 were given (and `cart_items` has no `owner_id`).

- [ ] **Step 3: Implement the schema, migration, and methods**

In `app/db.py`, split the `cart_items` DDL out of `SCHEMA` so the unique index is not created against a legacy table that lacks `owner_id`. Replace the current `SCHEMA` block (lines 8-48) with:

```python
CART_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS cart_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_key      TEXT NOT NULL,
    owner_id          TEXT NOT NULL,
    card_key          TEXT NOT NULL,
    card_name         TEXT NOT NULL,
    set_code          TEXT,
    collector_number  TEXT,
    store_id          TEXT NOT NULL,
    store_name        TEXT NOT NULL,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    variant_id        TEXT,
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
"""

CART_ITEMS_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_cart_items_owner_identity
    ON cart_items(owner_id, identity_key);
"""

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
CREATE TABLE IF NOT EXISTS cart_sessions (
    id         TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL
);
""" + CART_ITEMS_TABLE
```

Replace `init_schema` (lines 84-98) with:

```python
    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)
        self._migrate_cart_items()
        self._conn.executescript(CART_ITEMS_INDEX)
        self._conn.commit()

    def _migrate_cart_items(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(cart_items)")
        }
        if not existing:
            return
        if "owner_id" not in existing:
            # No way to drop a column-level UNIQUE via ALTER, and legacy rows
            # have no owner anyway: rebuild the table and discard them.
            self._conn.execute("DROP TABLE cart_items")
            self._conn.executescript(CART_ITEMS_TABLE)
            return
        self._add_missing_cart_columns()
```

Add the session methods (place them after `_add_missing_cart_columns`):

```python
    def create_session(self, owner_id: str) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT INTO cart_sessions (id, created_at, last_seen) VALUES (?,?,?)",
            (owner_id, now, now),
        )
        self._conn.commit()

    def touch_session(self, owner_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM cart_sessions WHERE id=?", (owner_id,)
        ).fetchone()
        if row is None:
            return False
        self._conn.execute(
            "UPDATE cart_sessions SET last_seen=? WHERE id=?",
            (time.time(), owner_id),
        )
        self._conn.commit()
        return True

    def purge_expired_sessions(self, cutoff: float) -> None:
        self._conn.execute(
            "DELETE FROM cart_sessions WHERE last_seen < ?", (cutoff,)
        )
        self._conn.execute(
            "DELETE FROM cart_items WHERE owner_id NOT IN "
            "(SELECT id FROM cart_sessions)"
        )
        self._conn.commit()
```

Now update the cart methods (lines 157-222) to take `owner_id` and scope every query:

```python
    def add_cart_item(self, owner_id: str, item: dict) -> tuple[int, bool]:
        existing = self._conn.execute(
            "SELECT id FROM cart_items WHERE owner_id=? AND identity_key=?",
            (owner_id, item["identity_key"]),
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False
        cur = self._conn.execute(
            "INSERT INTO cart_items (identity_key, owner_id, card_key, card_name, "
            "set_code, collector_number, store_id, store_name, title, url, "
            "variant_id, printing_hint, image_url, condition, finish, price_sgd, "
            "currency, in_stock, quantity, status, added_at, offer_fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["identity_key"], owner_id, item["card_key"], item["card_name"],
                item.get("set_code"), item.get("collector_number"),
                item["store_id"], item["store_name"], item["title"],
                item["url"], item.get("variant_id"), item.get("printing_hint"),
                item.get("image_url"), item.get("condition"), item["finish"],
                item["price_sgd"], item["currency"], int(item["in_stock"]),
                int(item["quantity"]), item.get("status", "ok"), time.time(),
                item["offer_fetched_at"],
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid), True

    def list_cart(self, owner_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM cart_items WHERE owner_id=? ORDER BY added_at ASC, id ASC",
            (owner_id,),
        ).fetchall()
        return [_cart_row(r) for r in rows]

    def get_cart_item(self, owner_id: str, item_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM cart_items WHERE id=? AND owner_id=?",
            (item_id, owner_id),
        ).fetchone()
        return _cart_row(row) if row is not None else None

    def update_cart_quantity(self, owner_id: str, item_id: int, quantity: int) -> dict | None:
        quantity = max(1, int(quantity))
        cur = self._conn.execute(
            "UPDATE cart_items SET quantity=? WHERE id=? AND owner_id=?",
            (quantity, item_id, owner_id),
        )
        self._conn.commit()
        return self.get_cart_item(owner_id, item_id) if cur.rowcount else None

    def update_cart_item(self, owner_id: str, item_id: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in CART_UPDATABLE}
        if not allowed:
            return self.get_cart_item(owner_id, item_id)
        assignments = ", ".join(f"{k}=?" for k in allowed)
        cur = self._conn.execute(
            f"UPDATE cart_items SET {assignments} WHERE id=? AND owner_id=?",
            (*allowed.values(), item_id, owner_id),
        )
        self._conn.commit()
        return self.get_cart_item(owner_id, item_id) if cur.rowcount else None

    def remove_cart_item(self, owner_id: str, item_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM cart_items WHERE id=? AND owner_id=?", (item_id, owner_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def clear_cart(self, owner_id: str) -> None:
        self._conn.execute("DELETE FROM cart_items WHERE owner_id=?", (owner_id,))
        self._conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (all tests, including the four updated cart tests).

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add per-owner cart storage and session table"
```

---

### Task 2: Session cookie resolution module

**Files:**
- Create: `app/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `Database.create_session`, `Database.touch_session`, `Database.purge_expired_sessions` from Task 1.
- Produces:
  - `CART_COOKIE: str` = `"mtg_cart"`
  - `DEFAULT_TTL_DAYS: int` = `30`
  - `CART_TTL_SECONDS: float` — module constant read once at import
  - `new_token() -> str`
  - `set_cart_cookie(response: Response, owner_id: str, ttl_seconds: float, secure: bool) -> None`
  - `resolve_owner(db: Database, request: Request, response: Response, ttl_seconds: float) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sessions.py`:

```python
from starlette.requests import Request
from starlette.responses import Response

from app.db import Database
from app.sessions import (
    CART_COOKIE,
    CART_TTL_SECONDS,
    DEFAULT_TTL_DAYS,
    _ttl_seconds_from_env,
    new_token,
    resolve_owner,
    set_cart_cookie,
)


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def _request(cookie=None, scheme="http"):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{CART_COOKIE}={cookie}".encode()))
    return Request(
        {"type": "http", "method": "GET", "path": "/api/cart",
         "scheme": scheme, "headers": headers}
    )


def test_token_is_random_and_urlsafe():
    assert new_token() != new_token()
    assert len(new_token()) >= 32


def test_ttl_reads_env_with_default_fallback(monkeypatch):
    monkeypatch.delenv("MTG_CART_TTL_DAYS", raising=False)
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "7")
    assert _ttl_seconds_from_env() == 7 * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "not-a-number")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "0")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400


def test_resolve_owner_mints_session_and_sets_cookie(tmp_path):
    db = _db(tmp_path)
    response = Response()
    owner = resolve_owner(db, _request(), response, CART_TTL_SECONDS)
    assert db.touch_session(owner) is True
    header = response.headers["set-cookie"]
    assert f"{CART_COOKIE}={owner}" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    db.close()


def test_resolve_owner_reuses_known_cookie(tmp_path):
    db = _db(tmp_path)
    first = resolve_owner(db, _request(), Response(), CART_TTL_SECONDS)
    again = resolve_owner(db, _request(cookie=first), Response(), CART_TTL_SECONDS)
    assert again == first
    db.close()


def test_resolve_owner_replaces_expired_cookie(tmp_path):
    db = _db(tmp_path)
    old = resolve_owner(db, _request(), Response(), CART_TTL_SECONDS)
    db._conn.execute("UPDATE cart_sessions SET last_seen=1.0 WHERE id=?", (old,))
    db._conn.commit()

    response = Response()
    new = resolve_owner(db, _request(cookie=old), response, 60)
    assert new != old
    assert db.touch_session(old) is False
    assert f"{CART_COOKIE}={new}" in response.headers["set-cookie"]
    db.close()


def test_set_cart_cookie_secure_flag():
    response = Response()
    set_cart_cookie(response, "tok", 100, secure=True)
    assert "Secure" in response.headers["set-cookie"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sessions'`.

- [ ] **Step 3: Implement `app/sessions.py`**

```python
from __future__ import annotations

import os
import secrets
import time

from starlette.requests import Request
from starlette.responses import Response

from app.db import Database

CART_COOKIE = "mtg_cart"
DEFAULT_TTL_DAYS = 30


def _ttl_seconds_from_env() -> float:
    raw = os.environ.get("MTG_CART_TTL_DAYS", "").strip()
    try:
        days = float(raw) if raw else float(DEFAULT_TTL_DAYS)
    except ValueError:
        days = float(DEFAULT_TTL_DAYS)
    if days <= 0:
        days = float(DEFAULT_TTL_DAYS)
    return days * 86400


#: Idle lifetime of an anonymous cart session. Read once at import; tests can
#: monkeypatch this attribute to exercise expiry.
CART_TTL_SECONDS: float = _ttl_seconds_from_env()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def set_cart_cookie(
    response: Response, owner_id: str, ttl_seconds: float, secure: bool
) -> None:
    response.set_cookie(
        key=CART_COOKIE,
        value=owner_id,
        max_age=int(ttl_seconds),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def resolve_owner(
    db: Database, request: Request, response: Response, ttl_seconds: float
) -> str:
    """Return the cart owner for this request, minting a session if needed.

    Expired sessions are purged first, so a cookie whose session sat idle past
    the TTL starts a fresh empty cart instead of being revived.
    """
    db.purge_expired_sessions(time.time() - ttl_seconds)
    token = request.cookies.get(CART_COOKIE)
    if token and db.touch_session(token):
        owner_id = token
    else:
        owner_id = new_token()
        db.create_session(owner_id)
    set_cart_cookie(response, owner_id, ttl_seconds, request.url.scheme == "https")
    return owner_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sessions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sessions.py tests/test_sessions.py
git commit -m "feat: add cart session cookie resolution"
```

---

### Task 3: Wire the owner into the cart API

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `app.sessions.resolve_owner`, `app.sessions.CART_TTL_SECONDS`; the owner-scoped `Database` methods from Task 1.
- Produces: a `cart_owner(request, response)` dependency declared by every `/api/cart*` route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (add `from app.db import Database` near the existing imports):

```python
def test_cart_is_isolated_per_browser(tmp_path):
    a = _client(tmp_path)
    b = _client(tmp_path)
    created = a.post("/api/cart", json=CART_PAYLOAD).json()

    assert a.get("/api/cart").json()["count"] == 1
    assert b.get("/api/cart").json()["count"] == 0

    # b cannot mutate a's line
    assert b.patch(f"/api/cart/{created['id']}", json={"quantity": 9}).status_code == 404
    assert b.delete(f"/api/cart/{created['id']}").status_code == 404
    assert a.get("/api/cart").json()["items"][0]["quantity"] == 2


def test_cart_sets_session_cookie(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/cart")
    header = response.headers["set-cookie"]
    assert "mtg_cart=" in header
    assert "HttpOnly" in header


def test_cart_survives_with_the_same_cookie(tmp_path):
    client = _client(tmp_path)
    client.post("/api/cart", json=CART_PAYLOAD)
    token = client.cookies.get("mtg_cart")

    other = _client(tmp_path)
    other.cookies.set("mtg_cart", token)
    assert other.get("/api/cart").json()["count"] == 1


def test_idle_session_is_purged(tmp_path, monkeypatch):
    import app.sessions as sessions

    client = _client(tmp_path)
    client.post("/api/cart", json=CART_PAYLOAD)
    old = client.cookies.get("mtg_cart")

    # age the session on disk, then make the TTL short so the next cart request
    # purges it before resolving the cookie
    db = Database(tmp_path / "t.db")
    db._conn.execute("UPDATE cart_sessions SET last_seen=1.0 WHERE id=?", (old,))
    db._conn.commit()
    db.close()

    monkeypatch.setattr(sessions, "CART_TTL_SECONDS", 60)
    response = client.get("/api/cart")

    assert client.cookies.get("mtg_cart") != old
    assert response.json()["count"] == 0
```

The existing single-client cart tests keep passing because a `TestClient` persists its cookie jar across requests. No edits needed to them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `test_cart_is_isolated_per_browser` sees `count == 1` for `b` (or a `TypeError` from the old un-scoped `db.list_cart()`).

- [ ] **Step 3: Implement the dependency and route changes**

In `app/main.py`, update the FastAPI import and add the sessions import:

```python
from fastapi import Depends, FastAPI, HTTPException, Request, Response
```

```python
import app.sessions as sessions
```

Inside `create_app`, immediately after `db.init_schema()` and the `registry` line, add:

```python
    def cart_owner(request: Request, response: Response) -> str:
        # Read the module attribute at call time so tests can shrink the TTL.
        return sessions.resolve_owner(db, request, response, sessions.CART_TTL_SECONDS)
```

Replace the cart routes (lines 161-198) with:

```python
    @app.get("/api/cart")
    def get_cart(owner_id: str = Depends(cart_owner)):
        items = db.list_cart(owner_id)
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

    @app.post("/api/cart")
    def add_to_cart(request: AddCartRequest, owner_id: str = Depends(cart_owner)):
        row = line_from_offer(request.card.model_dump(), request.offer.model_dump())
        item_id, created = db.add_cart_item(owner_id, row)
        return {"id": item_id, "created": created}

    @app.patch("/api/cart/{item_id}")
    def set_cart_quantity(
        item_id: int, request: QuantityRequest, owner_id: str = Depends(cart_owner)
    ):
        item = db.update_cart_quantity(owner_id, item_id, request.quantity)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.delete("/api/cart/{item_id}")
    def remove_from_cart(item_id: int, owner_id: str = Depends(cart_owner)):
        if not db.remove_cart_item(owner_id, item_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"ok": True}

    @app.delete("/api/cart")
    def clear_cart(owner_id: str = Depends(cart_owner)):
        db.clear_cart(owner_id)
        return {"ok": True}
```

Update the two remaining cart routes:

```python
    @app.patch("/api/cart/{item_id}/refresh")
    def refresh_cart_item(
        item_id: int,
        request: RefreshItemRequest,
        owner_id: str = Depends(cart_owner),
    ):
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
        item = db.update_cart_item(owner_id, item_id, **fields)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.post("/api/cart/refresh")
    async def refresh_cart(owner_id: str = Depends(cart_owner)):
        items = db.list_cart(owner_id)
        if not items:
            raise HTTPException(status_code=400, detail="Cart is empty")
        # ... (unchanged body from here: builds cards, selects stores, creates run)
```

Leave the rest of `refresh_cart` (the card/store/run construction and its return) exactly as it is today; only the first line and signature change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: scope cart API to the session cookie owner"
```

---

### Task 4: Document and expose the TTL setting

**Files:**
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md` (environment variables table)

**Interfaces:**
- Consumes: the `MTG_CART_TTL_DAYS` env var read by `app/sessions.py`.
- Produces: nothing importable; operator-facing configuration only.

- [ ] **Step 1: Add the env example entry**

Append to `.env.example`:

```bash
# Days an idle cookie-session cart survives before it and its lines are purged.
# Every cart request refreshes the timer. Default 30.
MTG_CART_TTL_DAYS=30
```

- [ ] **Step 2: Pass it through compose**

In `compose.yaml`, under `environment:`, after the `MTG_BYPARR_URL` line add:

```yaml
      MTG_CART_TTL_DAYS: ${MTG_CART_TTL_DAYS:-30}
```

- [ ] **Step 3: Document it in the README**

In the "Environment variables" table in `README.md`, add a row after `MTG_BYPARR_URL`:

```markdown
| `MTG_CART_TTL_DAYS` | `30` | Days an idle cart session survives before it and its lines are purged. Each cart request refreshes the timer. |
```

- [ ] **Step 4: Verify the app still imports and the suite passes**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .env.example compose.yaml README.md
git commit -m "docs: document the cart session TTL setting"
```

---

## Self-Review

**Spec coverage:**

- §4 Architecture — `app/sessions.py` (Task 2), `app/db.py` changes (Task 1), `cart_owner` dependency (Task 3), untouched frontend (no task needed).
- §5 Data model + migration — Task 1.
- §6 Cookie handling — Task 2 (`set_cart_cookie`, `resolve_owner`), Task 3 (dependency).
- §7 TTL + purge (purge-first ordering) — Task 1 (`purge_expired_sessions`), Task 2 (`resolve_owner` purges before resolving).
- §8 Endpoints — Task 3 (all seven routes scoped; delete returns 404 on mismatch).
- §9 Edge cases — Task 3 isolation/purge tests; unknown cookie covered by Task 2 `test_resolve_owner_replaces_expired_cookie`.
- §10 Testing — Tasks 1-3 tests.
- §11 Configuration — Task 4.

**Placeholder scan:** The only elided code is the unchanged tail of `refresh_cart`, explicitly marked "unchanged body from here"; all other steps carry full code.

**Type consistency:** `owner_id` is a `str` throughout; `remove_cart_item` returns `bool` in Task 1 and is checked for truthiness in Task 3; `purge_expired_sessions(cutoff: float)` is called with `time.time() - ttl_seconds` in both Tasks 1 and 2; `CART_TTL_SECONDS` is the monkeypatch target in Task 3 and the module constant defined in Task 2.

---

## Execution Handoff

After the tasks land, the branch is ready for the final whole-branch review before integration.
