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
    assert grey.requests_per_second == 0
    assert grey.attempts == 1
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


from app.politeness import RetryingHttpClient, make_store_client


def _config(**over):
    values = dict(
        id="greyogre", name="Grey Ogre Games", zone="singapore",
        adapter_path="app.adapters.greyogre:GreyOgreAdapter",
        base_url="https://www.greyogregames.com",
    )
    values.update(over)
    return StoreConfig(**values)


def test_build_adapters_wraps_client_per_store():
    adapters = build_adapters([_config(requests_per_second=0.5)], FakeHttp())
    client = adapters[0]._http
    assert isinstance(client, RetryingHttpClient)
    assert client.rate_per_second == 0.5


def test_build_adapters_uses_configured_attempts():
    adapters = build_adapters([_config(attempts=1)], FakeHttp())
    assert adapters[0]._http._policy.attempts == 1


def test_make_store_client_reflects_configured_rate():
    client = make_store_client(FakeHttp(), _config(requests_per_second=0.25), sleep=lambda _s: None)
    assert client.rate_per_second == 0.25
