import asyncio
from decimal import Decimal

from app.adapters.base import AdapterError, CardRequest, Offer
from app.db import Database
from app.orchestrator import run_search


class FakeAdapter:
    store_id = "fake"
    store_name = "Fake Store"
    zone = "singapore"
    needs_browser = False

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def search(self, card):
        self.calls += 1
        if self.fail:
            raise AdapterError("nope")
        return [Offer(
            store_id="fake", store_name="Fake Store", title=card.name,
            price_sgd=Decimal("2.50"), currency="SGD", condition="NM",
            finish="nonfoil", in_stock=True, url="https://x/p",
            printing_hint=None,
        )]


def _run(cards, adapters, db, **kwargs):
    async def collect():
        return [e async for e in run_search(cards, adapters, db, **kwargs)]
    return asyncio.run(collect())


def test_run_search_emits_results_and_done(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    events = _run([CardRequest(name="Sol Ring")], [FakeAdapter()], db)
    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert "result" in types
    assert types[-1] == "done"
    result = next(e for e in events if e["type"] == "result")
    assert result["offers"][0]["offer"]["price_sgd"] == "2.50"
    assert events[-1]["summary"]["errors"] == 0


def test_run_search_isolates_adapter_failure(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    events = _run([CardRequest(name="Sol Ring")], [FakeAdapter(fail=True)], db)
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["summary"]["errors"] == 1


def test_run_search_uses_cache_on_second_call(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    adapter = FakeAdapter()
    _run([CardRequest(name="Sol Ring")], [adapter], db)
    assert adapter.calls == 1
    events = _run([CardRequest(name="Sol Ring")], [adapter], db)
    assert adapter.calls == 1  # served from cache
    result = next(e for e in events if e["type"] == "result")
    assert result["cached"] is True
