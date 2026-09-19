from __future__ import annotations

SCYRSFALL_SETS_URL = "https://api.scryfall.com/sets"


def normalize_set_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


class SetResolver:
    """Maps Scryfall set names to set codes.

    Fetches the full set list once per instance and caches the name -> code
    mapping in memory. HTTP failures degrade to an empty map (no matches)
    rather than raising, so a Scryfall outage cannot break a store lookup.
    """

    def __init__(self, http):
        self._http = http
        self._by_name: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._by_name is None:
            try:
                payload = self._http.get_json(SCYRSFALL_SETS_URL)
            except Exception:
                self._by_name = {}
            else:
                mapping: dict[str, str] = {}
                for item in payload.get("data", []):
                    name = item.get("name")
                    code = item.get("code")
                    if name and code:
                        mapping[normalize_set_name(name)] = str(code).lower()
                self._by_name = mapping
        return self._by_name

    def code_for(self, set_name: str) -> str | None:
        return self._load().get(normalize_set_name(set_name))
