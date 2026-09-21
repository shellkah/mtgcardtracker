from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

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

# Bump when the cached Offer shape changes. The payload is opaque JSON, so an
# older row deserialises cleanly and silently misses any newly added field
# (image_url, for one). Stamping the version makes those rows a cache miss and
# they get refetched instead of serving incomplete offers until the TTL expires.
CACHE_VERSION = 3

# Columns added to cart_items after the table first shipped. ``CREATE TABLE IF
# NOT EXISTS`` will not alter an existing table, so init_schema applies these
# with ALTER TABLE, preserving any saved satchel rows.
CART_ADDED_COLUMNS = {"variant_id": "TEXT"}

CART_FIELDS = (
    "id", "identity_key", "card_key", "card_name", "set_code",
    "collector_number", "store_id", "store_name", "title", "url",
    "variant_id", "printing_hint", "image_url", "condition", "finish",
    "price_sgd", "currency", "in_stock", "quantity", "status", "added_at",
    "offer_fetched_at", "last_checked_at",
)

CART_UPDATABLE = {
    "price_sgd", "currency", "condition", "finish", "in_stock",
    "offer_fetched_at", "last_checked_at", "status", "variant_id",
}


def _cart_row(row) -> dict:
    return {field: row[field] for field in CART_FIELDS}


class Database:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

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

    def get_cached(self, store_id: str, card_key: str, ttl_seconds: int) -> dict | None:
        row = self._conn.execute(
            "SELECT payload, fetched_at FROM cache WHERE store_id=? AND card_key=?",
            (store_id, card_key),
        ).fetchone()
        if row is None:
            return None
        if ttl_seconds < 0 or time.time() - row["fetched_at"] > ttl_seconds:
            return None
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict) or payload.get("v") != CACHE_VERSION:
            return None
        return payload

    def set_cached(self, store_id: str, card_key: str, payload: dict) -> None:
        stamped = {**payload, "v": CACHE_VERSION}
        self._conn.execute(
            "INSERT INTO cache (store_id, card_key, payload, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(store_id, card_key) DO UPDATE SET payload=excluded.payload, "
            "fetched_at=excluded.fetched_at",
            (store_id, card_key, json.dumps(stamped), time.time()),
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

    def close(self) -> None:
        self._conn.close()
