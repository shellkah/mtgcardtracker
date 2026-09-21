import importlib

from fastapi.testclient import TestClient

from app.main import create_app


def _stores_yaml(tmp_path, store_id="greyogre"):
    stores = tmp_path / "stores.yaml"
    stores.write_text(
        "zones:\n  - id: singapore\n    name: Singapore\n"
        "stores:\n"
        f"  - id: {store_id}\n    name: Env Store\n    zone: singapore\n"
        "    adapter: app.adapters.greyogre:GreyOgreAdapter\n"
        "    base_url: https://example.test\n    enabled: true\n"
    )
    return stores


def test_healthz_ok(tmp_path):
    stores = _stores_yaml(tmp_path)
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_module_app_honours_env_paths(tmp_path, monkeypatch):
    stores = _stores_yaml(tmp_path, store_id="envstore")
    monkeypatch.setenv("MTG_CONFIG_PATH", str(stores))
    monkeypatch.setenv("MTG_DB_PATH", str(tmp_path / "env.db"))

    import app.main as main_module

    importlib.reload(main_module)
    client = TestClient(main_module.app)
    data = client.get("/api/stores").json()
    assert data["stores"][0]["id"] == "envstore"
    assert (tmp_path / "env.db").exists()
