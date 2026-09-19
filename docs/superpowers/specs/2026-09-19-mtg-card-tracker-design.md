# MTG Card Tracker — Design

**Date:** 2026-09-19
**Status:** Approved design, pending implementation plan

## 1. Summary

A local, single-user web application that takes a list of Magic: The Gathering
cards exported from Moxfield or ManaBox, searches a configured set of local card
sellers (starting with Singapore), and displays which stores sell each card, at
what price, in what condition, and whether it is foil.

The tool is built around a **plugin adapter** model: each store is a small Python
module implementing one interface, and a development agent adds stores one at a
time by crawling the site, writing the adapter, and recording golden fixtures.

## 2. Goals

- Local single-user web app: paste a card list, run a search, get a per-card
  offer comparison across configured stores.
- Exact-printing matching when the export carries set code + collector number;
  name-only fallback otherwise.
- Show price (SGD), condition, and foil/finish status per offer, plus a link to
  the store listing.
- A clean adapter interface so one store can be added at a time
  (crawl -> write adapter -> record fixtures -> contract test).
- Zones as a first-class grouping so non-Singapore stores can be added later.

## 3. Non-goals (v1)

- No price history or trend charts.
- No buying/cart automation or checkout.
- No user accounts or multi-user support.
- No shipping-cost optimizer or full multi-store basket optimization.
- No anti-bot evasion beyond the opt-in headless browser.
- Multi-currency is out of scope: v1 assumes all prices are SGD.

## 4. Stack

- **Backend:** Python, FastAPI, serving both the JSON/SSE API and the frontend.
- **Frontend:** Plain HTML/CSS/vanilla JS, **no build step**.
- **Streaming:** Server-Sent Events (SSE).
- **Storage:** SQLite (response cache, saved lists; no price history).
- **Fetching:** HTTP-first via `httpx`; Playwright opt-in per adapter
  (`needs_browser = True`).
- **Card catalog:** Scryfall API, cached locally, for name/set/printing
  normalization and validation.
- **Concurrency:** synchronous adapters executed in a bounded thread pool.

## 5. Architecture

Single Python process plus a CLI for adapter development.

```
mtgcardtracker/
  app/
    main.py                 # FastAPI app, routes, SSE stream
    orchestrator.py         # run lifecycle: fan out cards x stores, stream results
    matching.py             # CardRequest <-> Offer matching rules
    parsing.py              # Moxfield / ManaBox export -> CardRequest[]
    scryfall.py             # canonical card/printing lookup (names, set codes)
    db.py                   # SQLite: cache, saved lists, runs
    adapters/
      base.py               # Adapter protocol + Offer/CardRequest models
      shopify.py            # ShopifyAdapter base (covers most stores)
      registry.py           # loads config/stores.yaml -> adapter instances
      greyogre.py           # thin subclass (store-specific quirks only)
      gameshaven.py
      cardscitadel.py
      hideout.py
      cardarena.py
      agorahobby.py         # custom HTML/JSON adapter
      duellerspoint.py      # custom adapter
  config/stores.yaml        # id, zone, adapter, base_url, enabled, options
  fixtures/                 # recorded raw responses per store, for tests
  web/                      # templates + static (plain HTML/CSS/JS, SSE client)
  cli.py                    # `explore`, `scaffold`, `probe`, `validate`
```

### 5.1 Store platform recon (initial findings)

| Store | URL | Platform | Notes |
|---|---|---|---|
| Grey Ogre Games | greyogregames.com | Shopify | `/products.json` confirmed |
| Games Haven | gameshaventcg.com | Shopify | products.json confirmed |
| Cards Citadel | cardscitadel.com | Shopify | products.json confirmed |
| Hideout | hideoutcg.com | Shopify-like | collections-based; verify |
| Card Arena | cardarena.sg | Shopify-like | verify |
| Dueller's Point | duellerspoint.com | CrystalCommerce-style | products.json shape differs |
| Agora Hobby | agorahobby.com | Custom (Nekorin) | has its own multi-card search |

Platform detection is a dev-time concern handled by `cli.py explore`, not
hardcoded in the app.

## 6. Data model

### 6.1 CardRequest

One unique requested card (duplicate export lines with the same printing are
merged; quantities summed).

```python
@dataclass
class CardRequest:
    name: str
    set_code: str | None
    collector_number: str | None
    quantity: int = 1
    condition: str | None = None      # from export, if any
    foil: bool | None = None          # from export, if any
```

### 6.2 Offer

One purchasable listing (typically a variant) returned by an adapter.

```python
@dataclass
class Offer:
    store_id: str
    store_name: str
    title: str                        # raw listing title
    price_sgd: Decimal
    currency: str                     # "SGD" (v1 assumption)
    condition: str | None             # NM, LP, MP, HP, DMG, or None
    finish: Literal["nonfoil", "foil", "etched", "surge", "unknown"]
    in_stock: bool
    url: str
    printing_hint: str | None         # set code / collector no. recovered from listing
    fetched_at: datetime
```

### 6.3 MatchResult

Produced by `matching.py`, wrapping an offer with a confidence for the requested
card.

```python
class MatchConfidence(Enum):
    EXACT = "exact"            # set code + collector number match
    NAME_ONLY = "name_only"    # matched by name; printing unspecified
    AMBIGUOUS = "ambiguous"    # name matches more than one candidate and no printing given
```

### 6.4 SQLite schema (v1)

- `cache(store_id, card_key, payload_json, fetched_at, PRIMARY KEY(store_id, card_key))`
  — `card_key` is the normalized card identity (set+collector if present, else
  lowercased name).
- `saved_lists(id, name, raw_text, format, created_at)`
- `stores_config_snapshot(id, payload_json, updated_at)` — optional, for debugging.

No price-history table in v1.

## 7. Adapter interface

```python
class Adapter(Protocol):
    store_id: str
    zone: str
    needs_browser: bool

    def search(self, card: CardRequest) -> list[Offer]:
        ...
```

- **`ShopifyAdapter`** implements `search` generically: uses Shopify's search
  endpoint and product JSON, and parses each variant into
  `(condition, finish, price)` — handling the multi-variant reality of card
  stores once, in the base class.
- **Custom adapters** implement `search` directly, using a shared HTTP helper
  and, when `needs_browser`, an injected Playwright page.
- **Adapters do not do matching.** They return raw offers with a
  `printing_hint`; `matching.py` decides exact vs name-fallback vs ambiguous.
- Adapters are **synchronous** and are run in a thread pool.
- The registry reads `config/stores.yaml` and imports the named adapter class.

### 7.1 stores.yaml shape (illustrative)

```yaml
zones:
  - id: singapore
    name: Singapore
stores:
  - id: greyogre
    name: Grey Ogre Games
    zone: singapore
    adapter: app.adapters.greyogre:GreyOgreAdapter
    base_url: https://www.greyogregames.com
    enabled: true
    requests_per_second: 1
    concurrency: 2
  - id: agorahobby
    name: Agora Hobby
    zone: singapore
    adapter: app.adapters.agorahobby:AgoraHobbyAdapter
    base_url: https://agorahobby.com
    enabled: true
    needs_browser: true
```

## 8. Input parsing

- **Moxfield CSV** and **ManaBox CSV**, detected by header row.
- **Plain-text fallback** (`1 Sol Ring`, `4x Lightning Bolt`) accepted as
  name-only; results are marked `NAME_ONLY`.
- Duplicate lines for the same printing are merged (quantities summed).
- Scryfall is consulted only to fill gaps and validate printings; we do not
  guess a printing when the export does not specify one.

## 9. Orchestration, caching, politeness

- **Fan-out:** for each `CardRequest` x enabled store, dispatch `search` into a
  bounded `ThreadPoolExecutor`; results are pushed onto an async queue feeding
  the SSE stream.
- **Per-store concurrency cap and minimum request delay** configured in
  `stores.yaml`.
- **Timeouts and limited retries with backoff** on transient failures.
- **Cache:** keyed on `(store_id, card_key)`, default TTL 6h (configurable).
  Served from cache when fresh; a "force refresh" toggle bypasses it.
- **Isolated failures:** an error for one `(card, store)` never aborts the run;
  the UI shows an error chip and the summary counts successes/failures.
- **Identification:** descriptive `User-Agent`; no proxy rotation or evasion.

## 10. Web UI

1. **Input screen** — paste/upload a list (auto-detected format), choose zone(s)
   and stores, set default condition/finish filters, press Search.
2. **Results screen (streaming)** — one row per card, populated as offers
   arrive. Expanding a row shows offers sorted cheapest-first with price,
   condition, finish, stock, and a link; the cheapest matching offer is
   highlighted. Live filters for condition, finish, and in-stock. A summary
   strip shows `found/total` and per-store status.
3. **Saved lists** — list, load, and delete saved lists from SQLite.

Frontend is plain HTML/CSS/vanilla JS with an `EventSource` SSE client. No build
step.

## 11. Developer workflow & tooling (CLI)

- `python cli.py explore <url>` — fetch the page (probe `/products.json` for
  Shopify), detect platform, print a summary, dump HTML/JSON for inspection.
- `python cli.py scaffold <store_id> --platform shopify|custom` — write the
  adapter module, register it in `stores.yaml`, create a fixture-backed test
  skeleton.
- `python cli.py probe <store_id> "<card name>"` — run one adapter end-to-end
  against a real card, pretty-print parsed offers, and record the raw response
  into `fixtures/<store_id>/`.
- `python cli.py validate` — replay all fixtures through all adapters.

## 12. Testing

- **Unit:** CSV/plain-text parsing; condition/finish/price parsing; matching
  rules (exact vs name-only vs ambiguous).
- **Adapter contract tests:** recorded fixtures per store, offline and
  deterministic. This is the regression net when a site changes.
- **Live smoke tests:** `pytest -m live`, opt-in only, excluded from the default
  run.
- **Orchestration/API:** a fake adapter + fixture list driven through the SSE
  endpoint, asserting emitted events.
- **Cache/DB:** TTL behavior and saved-list round-trips.

## 13. Open questions / assumptions

- **Currency:** v1 assumes SGD everywhere; stores that display USD (e.g. Agora's
  currency toggle) must be configured/handled to yield SGD.
- **Card Arena / Hideout:** platform not fully confirmed; `explore` verifies
  during implementation.
- **Scryfall rate limits:** respected via local caching; bulk data may be used
  later if lookups get heavy.
- **Robots/ToS:** request rates are kept low and identifying; if a store's
  terms forbid scraping, that store is disabled rather than worked around.

## 14. Milestones (implementation sequence)

1. Project skeleton: FastAPI app, `CardRequest`/`Offer`/`Adapter` models,
   `stores.yaml` + registry, SQLite + cache.
2. Input parsing (Moxfield/ManaBox CSV + plain text) with unit tests.
3. ShopifyAdapter base + first store (Grey Ogre) with fixtures and contract test.
4. Orchestrator + SSE endpoint + results UI (streaming), using a fake adapter.
5. Matching rules (exact vs name-only vs ambiguous) with tests.
6. Remaining Shopify stores; then custom adapters (Agora Hobby, Dueller's Point).
7. Saved lists, filters polish, CLI (`explore`/`scaffold`/`probe`/`validate`).

Milestone 4 can be exercised end-to-end with the fake adapter before real stores
are wired, so the UI is not blocked on scraping.
