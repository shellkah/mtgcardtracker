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
