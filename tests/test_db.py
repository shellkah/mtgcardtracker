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
