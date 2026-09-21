# MTG Card Tracker

A local, single-user web app that takes a Magic: The Gathering card list
exported from Moxfield or ManaBox, searches a configured set of local card
sellers, and shows which stores stock each card — with price (SGD), condition
(NM/LP/MP/HP/DMG), and foil status.

Stores are pluggable adapters, grouped by zone. v1 ships with thirteen
Singapore stores — Grey Ogre Games, Games Haven, Cards Citadel, Hideout, One
MTG, MTG Asia, Mana Pro, Card Affinity, Hideyoshi TCG, Flagship Games, 5 Mana,
Fyendal Hobby, and Arcane Sanctum — and a documented procedure for adding more.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages the Python toolchain and venv)
- Python >= 3.11 (pinned to 3.12 in `.python-version`; uv fetches it if missing)
- No JS build tooling — the frontend is plain HTML/CSS/JS

## Setup

```bash
uv sync   # creates .venv and installs runtime + dev dependencies from uv.lock
```

Dependencies live in `pyproject.toml` and are pinned in `uv.lock` (commit this
file). `uv sync` installs the `dev` group (pytest) by default; use
`uv sync --no-dev` for a runtime-only environment. To run a bare command inside
the environment, use `uv run <cmd>`.

## Run

The app reads `config/stores.yaml` and writes `mtgcardtracker.db` **relative to
the current working directory**, so run it from the repository root:

```bash
uv run uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000>. Paste a Moxfield/ManaBox CSV (or a plain
`1 Sol Ring` list), tick the stores, and press Search. Results stream in per
card; expand offers and change the condition/finish/in-stock filters live. Saved
lists persist in SQLite. Add any offer to **the Satchel** — a persistent basket
in the top bar. It groups your saved offers by store, totals them (SGD), and can
re-check their prices and stock on demand. There is no checkout; the satchel is
a saved basket you buy from manually.

## Card list formats

- **Moxfield CSV** (`Count, Name, Edition, Collector Number, Foil, Condition, …`)
- **ManaBox CSV** (`Name, Set code, Collector number, Foil, Quantity, …`)
- **Plain text** (`1 Sol Ring`, `4x Lightning Bolt`) — name-only matching

Matching is exact-printing when the export carries a set code + collector
number, falling back to name-only matches (which may span printings) otherwise.

## Adding a store (Milestone 6 procedure)

Each store is one adapter with an offline contract test backed by recorded
fixtures.

1. **Recon:** `uv run python cli.py explore https://<store>` — detects the
   platform and dumps the response for inspection.
2. **Scaffold:** `uv run python cli.py scaffold <store_id> --base-url https://<store> --platform shopify|custom`
   — appends a disabled entry to `config/stores.yaml`.
3. **Write the adapter.** Shopify stores extend `app/adapters/shopify.py`; custom
   stores implement the `Adapter` protocol (`app/adapters/base.py`). Adapters are
   synchronous and must not do matching or caching.
4. **Record fixtures:** `uv run python cli.py probe <store_id> "<card>"`
   and/or save the raw response under `tests/fixtures/<store_id>/`.
5. **Contract test:** add `tests/test_<store>_adapter.py` replaying the fixture
   and asserting price/condition/finish/stock/printing hint.
6. **Enable** the store (`enabled: true`) only once its contract test passes.

## Layout

```
app/
  main.py            FastAPI app, routes, SSE stream, saved lists
  runs.py            in-memory run registry + SSE subscription
  orchestrator.py    card x store fan-out, cache, event stream
  matching.py        requested printing <-> offer matching
  parsing.py         Moxfield / ManaBox / plain-text parsing
  db.py              SQLite cache + saved lists
  cart.py            cart domain helpers (identity, grouping, totals)
  http.py            HttpClient abstraction (identifying User-Agent)
  sets.py            Scryfall set-name -> set-code resolver
  scryfall.py        best-effort printing validation (currently dormant)
  filters.py         condition/finish/stock filtering (canonical)
  devtools.py        platform detection + response dumping
  adapters/
    base.py          CardRequest / Offer / Adapter protocol
    registry.py      config loading + adapter construction
    shopify.py       shared Shopify adapter base
    greyogre.py      Grey Ogre Games (Shopify)
    gameshaven.py    Games Haven (Shopify)
    cardscitadel.py  Cards Citadel (Shopify)
    hideout.py       Hideout (Shopify)
    onemtg.py        One MTG (Shopify)
    mtgasia.py       MTG Asia (Shopify)
    manapro.py       Mana Pro (Shopify)
    cardaffinity.py  Card Affinity (Shopify)
    hideyoshi.py     Hideyoshi TCG (Shopify)
    flagship.py      Flagship Games (Shopify)
    fivemana.py      5 Mana (Shopify)
    fyendalhobby.py  Fyendal Hobby (Shopify)
    arcanesanctum.py Arcane Sanctum (Shopify, title-keyed conditions)
    shopify_parse.py pure listing parsers (condition/finish/price/hint)
config/stores.yaml   zones + stores (id, adapter, base_url, concurrency, …)
cli.py               explore / scaffold / probe / validate
web/                 plain HTML/CSS/JS frontend
tests/               unit + fixture-backed adapter contract tests
docs/superpowers/    design spec + implementation plan
```

## Testing

```bash
uv run pytest -v
```

Adapter contract tests replay recorded fixtures and run offline. The `live`
pytest marker is reserved for opt-in network smoke tests
(`uv run pytest -m live`).

## Shopify adapter: why variants come from `/products/{handle}.js`

The shared Shopify adapter initially read variant data from
`/search/suggest.json`, assuming its product results carried a populated
`variants` array. Live verification against Grey Ogre Games (during the final
review) showed that endpoint returns **`variants: []`** — so the adapter would
have silently reported **zero offers for every card**, the worst possible
failure mode for a price comparison tool.

The correct source is the public per-product endpoint
`GET {base_url}/products/{handle}.js`:

- `variants[].title` is the condition, optionally suffixed ` Foil`
  (e.g. `Near Mint`, `Lightly Played Foil`)
- `variants[].price` is an **integer in cents** (e.g. `1925` = SGD 19.25)
- `variants[].available` is the stock flag

The adapter now:

1. searches via `/search/suggest.json` (title/handle/url only),
2. filters to **name-matching** products before fetching anything else, and
3. fetches `/products/{handle}.js` only for those, dividing cents by 100 and
   isolating failures per product.

Fixtures under `tests/fixtures/greyogre/` are recorded from the live store, not
hand-authored. See the design spec and plan under `docs/superpowers/`.

## Known limitations / follow-ups

These were triaged by the final whole-branch review as follow-up work rather
than merge blockers.

- **Politeness.** Per-store pacing is currently **disabled**
  (`requests_per_second: 0`) because throttling did not stop stores from
  returning HTTP `429`. Retry behaviour is configured per store via `attempts`
  (total attempts, including the first): every store ships with `attempts: 1`,
  so there are no retries and transient failures surface immediately. Raising
  `attempts` re-enables bounded retry with exponential backoff, jitter, and
  `Retry-After` support. A Cloudflare challenge is named in the resulting store
  error. Per-store `concurrency` is still unused, and there is no aggregate
  cross-store request cap.
- **Per-run client/resolver construction.** Every search builds a fresh
  `HttpxClient` and `SetResolver`, re-fetching Scryfall's `/sets` per store, and
  never closes the httpx clients. Should become app-lifetime singletons closed
  on shutdown, with the set list cached in SQLite.
- **Set-name aliasing.** Store titles use names like `Secret Lair Drop Series`
  while Scryfall uses `Secret Lair Drop`, so `format:printing_hint` resolution
  falls back to name-only for those. An alias/normalization pass in
  `SetResolver` would recover exact matching. The Grey Ogre fixtures map
  `Secret Lair Drop Series` for deterministic exact-match coverage, so live
  name-only degradation for unresolvable names is not yet pinned by a test.
- **No README/run docs** — addressed by this file.
- **`app/scryfall.py` is dormant.** `ScryfallClient` is built and tested but not
  called from any flow. Either wire it (validate printings, fill set codes for
  plain lists) or treat it as an explicitly dormant helper.
- **SSE end-to-end offer assertion missing.** `tests/test_api.py`'s fake adapter
  returns no offers, so no test asserts a `result` event carrying offers through
  the HTTP/SSE layer; orchestrator tests cover that below the API.
- **No logging.** Cache failures and unexpected adapter errors are swallowed and
  only surface via SSE error events.
- **More Singapore stores.** Agora Hobby and Mox & Lotus need bespoke adapters
  (custom storefronts with no Shopify-style JSON API), and Dueller's Point is
  CrystalCommerce-shaped rather than Shopify. Cards Central is a custom Astro
  store behind a Vercel bot check.
- **Arcane Sanctum's condition suffix.** Unlike the other BinderPOS stores,
  Arcane Sanctum encodes condition and finish in the product title
  (``Sol Ring — NM Foil [Judge Gift Cards 2005]``) with a single
  ``Default Title`` variant. ``ArcaneSanctumAdapter._product_key`` strips that
  suffix so exact-name matching still works; the shared offer mapping then reads
  condition/finish from the full title.

## v1 constraints

- Prices are assumed to be **SGD**; no currency conversion.
- Response cache TTL defaults to 6 hours (`force refresh` bypasses it).
- No price history, no accounts. A cart (the Satchel) saves offers for manual
  purchase, but there is no checkout or order automation.
- No proxy rotation or anti-bot evasion — requests are low-rate and use an
  identifying `User-Agent`.
