from __future__ import annotations

from app.adapters.base import AdapterError, CardRequest, Offer


class GreyOgreAdapter:
    store_id: str
    store_name: str
    zone: str
    needs_browser = False

    def __init__(self, config, http):
        self.store_id = config.id
        self.store_name = config.name
        self.zone = config.zone
        self._config = config
        self._http = http

    def search(self, card: CardRequest) -> list[Offer]:
        raise AdapterError("not implemented")
