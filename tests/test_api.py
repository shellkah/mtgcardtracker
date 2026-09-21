from fastapi.testclient import TestClient

from app.db import Database
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
    # Keep one event loop alive for the background run task: production ASGI
    # servers use a single persistent loop, and TestClient does too when used as
    # a context manager. Without this the POST loop closes and the run task is
    # torn down before the stream connects.
    with client:
        run = client.post(
            "/api/runs",
            json={"text": "1 Sol Ring", "store_ids": ["greyogre"], "force_refresh": True},
        ).json()
        assert run["card_count"] == 1
        with client.stream("GET", f"/api/runs/{run['run_id']}/stream") as response:
            body = "".join(response.iter_text())
    assert '"type": "done"' in body


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
    assert item["variant_id"] == "41984399442000"

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
