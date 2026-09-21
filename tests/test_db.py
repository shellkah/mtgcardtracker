import json
import time

from app.db import CACHE_VERSION, Database


def test_cache_roundtrip_and_ttl(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.set_cached("greyogre", "ltc:409", {"offers": []})
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=3600) == {
        "offers": [], "v": CACHE_VERSION,
    }
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=-1) is None
    assert db.get_cached("other", "ltc:409", ttl_seconds=3600) is None
    db.close()


def test_cache_ignores_payloads_from_an_older_schema(tmp_path):
    """A row written before a field was added must not be served as a hit.

    Payloads are opaque JSON, so an old row loads fine and simply lacks the new
    field. The stored version stamp is what turns that into a miss.
    """
    db = Database(tmp_path / "t.db")
    db.init_schema()
    stale = json.dumps({"offers": [{"store_id": "s", "title": "no image_url"}]})
    db._conn.execute(
        "INSERT INTO cache (store_id, card_key, payload, fetched_at) VALUES (?,?,?,?)",
        ("greyogre", "ltc:409", stale, time.time()),
    )
    db._conn.commit()
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=3600) is None

    # and a payload with no version key at all is likewise a miss
    db._conn.execute(
        "INSERT OR REPLACE INTO cache (store_id, card_key, payload, fetched_at) "
        "VALUES (?,?,?,?)",
        ("greyogre", "ltc:409", json.dumps({"offers": [], "v": CACHE_VERSION - 1}),
         time.time()),
    )
    db._conn.commit()
    assert db.get_cached("greyogre", "ltc:409", ttl_seconds=3600) is None
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


from app.cart import line_from_offer


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


def test_cart_add_dedupes_identical_offers(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, created = db.add_cart_item("owner-a", _row())
    assert created is True
    again_id, again_created = db.add_cart_item("owner-a", _row())
    assert again_created is False
    assert again_id == item_id
    assert len(db.list_cart("owner-a")) == 1
    db.close()


def test_cart_quantity_update_and_remove(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, _ = db.add_cart_item("owner-a", _row())
    updated = db.update_cart_quantity("owner-a", item_id, 5)
    assert updated["quantity"] == 5
    assert db.update_cart_quantity("owner-a", item_id, 0)["quantity"] == 1  # clamped
    assert db.update_cart_quantity("owner-a", 9999, 2) is None
    db.remove_cart_item("owner-a", item_id)
    assert db.list_cart("owner-a") == []
    db.close()


def test_cart_refresh_update_and_clear(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    item_id, _ = db.add_cart_item("owner-a", _row())
    updated = db.update_cart_item(
        "owner-a", item_id, status="unavailable", last_checked_at=123.0
    )
    assert updated["status"] == "unavailable"
    assert updated["price_sgd"] == "4.50"  # untouched
    assert updated["last_checked_at"] == 123.0
    # unknown columns are ignored rather than raising
    assert db.update_cart_item("owner-a", item_id, bogus="x")["id"] == item_id
    db.clear_cart("owner-a")
    assert db.list_cart("owner-a") == []
    db.close()


def test_cart_persists_variant_id(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    db.add_cart_item("owner-a", _row())
    assert db.list_cart("owner-a")[0]["variant_id"] == "41984399442000"
    db.close()


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
