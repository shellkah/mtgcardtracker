from pathlib import Path

from app.adapters.registry import StoreConfig, build_adapters, load_config


class FakeHttp:
    def get_json(self, url, params=None):
        return {"resources": {"results": {"products": []}}}

    def get_text(self, url, params=None):
        return ""


def test_load_config_parses_stores():
    configs = load_config(Path("config/stores.yaml"))
    grey = next(c for c in configs if c.id == "greyogre")
    assert grey.zone == "singapore"
    assert grey.requests_per_second == 1.0
    assert grey.needs_browser is False


def test_build_adapters_imports_adapter_class():
    configs = [StoreConfig(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )]
    adapters = build_adapters(configs, FakeHttp())
    assert len(adapters) == 1
    assert adapters[0].store_id == "greyogre"
