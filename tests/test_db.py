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
