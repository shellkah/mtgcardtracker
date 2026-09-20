from fastapi.testclient import TestClient

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
