from __future__ import annotations

from app.http import HttpError

API = "https://api.scryfall.com"


class ScryfallClient:
    def __init__(self, http):
        self._http = http

    def validate_printing(self, set_code: str, collector_number: str) -> bool:
        url = f"{API}/cards/{set_code.strip().lower()}/{collector_number.strip().lower()}"
        try:
            payload = self._http.get_json(url)
        except HttpError:
            return False
        return bool(payload.get("set"))

    def find_printings(self, name: str) -> list[dict]:
        url = f"{API}/cards/search"
        params = {"q": f'!"{name}"', "unique": "prints"}
        try:
            payload = self._http.get_json(url, params=params)
        except HttpError:
            return []
        return [
            {"set": str(card.get("set", "")).lower(),
             "collector_number": str(card.get("collector_number", "")).lower()}
            for card in payload.get("data", [])
        ]
