from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_index_served(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    response = client.get("/")
    assert response.status_code == 200
    assert "MTG Card Tracker" in response.text


def test_app_js_served(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    assert client.get("/app.js").status_code == 200


def test_cart_drawer_present(tmp_path):
    stores = tmp_path / "stores.yaml"
    stores.write_text("zones: []\nstores: []\n")
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    html = client.get("/").text
    assert 'id="cart-button"' in html
    assert 'id="cart-drawer"' in html
    assert 'id="cart-body"' in html
