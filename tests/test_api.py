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
