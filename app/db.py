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

# Bump when the cached Offer shape changes. The payload is opaque JSON, so an
# older row deserialises cleanly and silently misses any newly added field
# (image_url, for one). Stamping the version makes those rows a cache miss and
# they get refetched instead of serving incomplete offers until the TTL expires.
CACHE_VERSION = 2


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

    def close(self) -> None:
        self._conn.close()
