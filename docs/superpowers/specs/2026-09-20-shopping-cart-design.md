# MTG Card Tracker — Shopping Cart ("The Satchel")

**Date:** 2026-09-20
**Status:** Approved design, pending implementation plan
**Parent spec:** `docs/superpowers/specs/2026-09-19-mtg-card-tracker-design.md`

## 1. Summary

Add a persistent, single shopping cart to the tracker. It lets the user **save
specific offers** they have found in the search results, then group and total
those saved offers by store so they can go and buy manually. There is **no
checkout and no purchase automation** — the cart is a basket builder and a
bookmark of chosen listings.

The cart is called **The Satchel** in the UI, extending the existing
"Cartomancer's Ledger" metaphor (the hunt, the ledger, the satchel that holds
what you caught).

## 2. Goals

- Save a specific offer (a particular store's listing, with its price,
  condition, finish, stock and URL) to a persistent basket.
- Edit per-line quantity; multiply prices by quantity.
- Group the basket by store, with per-store subtotals and a grand total.
- Re-check saved prices/stock on demand against the live stores.
- Keep the saved basket across page reloads and server restarts.
- Flag duplicate choices and unavailable/out-of-stock lines rather than hiding
  or silently rewriting them.

## 3. Non-goals

- No checkout, order placement, or store-side cart automation.
- No multiple/named carts or wishlists — exactly one basket.
- No automatic price re-checking on view; refresh is an explicit user action.
- No currency conversion (unchanged: SGD only).
- No price-history tracking; a line keeps only its current and last-known price.

## 4. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Purpose | A basket to buy from | One chosen offer per card accumulates toward a manual purchase. |
| Persistence | SQLite | Survives reload/restart; matches the single-user local model and `saved_lists`. |
| Freshness | Snapshot + manual "Re-check prices" | Saved prices are honest about their capture time; re-checking is deliberate, not automatic. |
| Quantity | Defaults to the card's list quantity, editable in the cart | Matches what the user pasted; lets them adjust. |
| Duplicates | Allow both offers for a card, flag as a duplicate choice | The comparison is the point; nothing is silently lost. |
| Placement | Slide-over drawer from a top-bar button | Non-disruptive to the existing two-column search/results layout. |
| Organisation | Grouped by store | Maps directly to "the orders I'll place". |
| Cart count | One cart | Matches "shopping cart"; no naming/managing UI. |
| Offer disappears on re-check | Mark unavailable, keep the line | The user decides whether to remove or replace it. |
| Add affordance | Per-offer `+` button plus "Add best" on the card row | Fast path and deliberate path both available. |
| Refresh reconciliation | Client-driven, reusing the run/SSE pipeline | Least new server surface; reuses the proven run/SSE path and existing offer handling. |

## 5. Architecture

The feature follows the existing layering: SQLite storage (`app/db.py`), pure
domain helpers (new `app/cart.py`), HTTP routes (`app/main.py`), and the plain
HTML/CSS/JS front end (`web/`). No new frameworks, no build step.

```
app/
  cart.py      # NEW: identity key, duplicate detection, store grouping, totals
  db.py        # + cart_items schema and cart methods
  main.py      # + /api/cart routes and /api/cart/refresh
web/
  index.html   # + top-bar satchel trigger, drawer <dialog>, new icons
  app.js       # + cart state, API calls, drawer rendering, refresh SSE, add buttons
  styles.css   # + satchel drawer, line items, badge, alerts
```

### 5.1 Data model — `cart_items`

One row per saved offer, in the style of the existing `saved_lists` table.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `identity_key` | TEXT NOT NULL **UNIQUE** | `store_id \| title \| condition \| finish \| url`; dedupes the identical offer |
| `card_key` | TEXT NOT NULL | `CardRequest.key` of the requested card; groups lines and drives refresh |
| `card_name` | TEXT NOT NULL | card snapshot |
| `set_code` | TEXT | card snapshot, used to rebuild `CardRequest`s |
| `collector_number` | TEXT | card snapshot, used to rebuild `CardRequest`s |
| `store_id` | TEXT NOT NULL | offer store |
| `store_name` | TEXT NOT NULL | offer store |
| `title` | TEXT NOT NULL | raw listing title |
| `url` | TEXT NOT NULL | listing URL |
| `printing_hint` | TEXT | offer snapshot |
| `image_url` | TEXT | offer snapshot (store photo) |
| `condition` | TEXT | NM/LP/MP/HP/DMG or NULL |
| `finish` | TEXT NOT NULL | nonfoil/foil/etched/surge/unknown |
| `price_sgd` | TEXT NOT NULL | stored as text to preserve `Decimal` |
| `currency` | TEXT NOT NULL | e.g. `SGD` |
| `in_stock` | INTEGER NOT NULL | 0/1 |
| `quantity` | INTEGER NOT NULL DEFAULT 1 | editable |
| `status` | TEXT NOT NULL DEFAULT 'ok' | `ok` \| `unavailable` |
| `added_at` | REAL NOT NULL | |
| `offer_fetched_at` | TEXT NOT NULL | the offer's `fetched_at` at capture |
| `last_checked_at` | REAL | set by a successful re-check |

**Identity rule:** `identity_key = f"{store_id}|{title}|{condition}|{finish}|{url}"`,
mirroring the front end's existing `offerIndexKey`. Adding an offer whose
identity already exists is a no-op (surfaced as an "already in the satchel"
toast). Adding a *different* offer for the same card is allowed and flagged as a
duplicate choice.

### 5.2 `app/cart.py` helpers (pure, testable)

- `identity_key(offer: dict) -> str`
- `group_by_store(items: list[dict]) -> list[dict]` — store sections with line
  items and subtotals.
- `duplicate_card_keys(items) -> set[str]` — cards with more than one line.
- `totals(items) -> dict` — grand total and excluded-line count, counting only
  `status == "ok"` and `in_stock` lines.
- `line_from_offer(card: dict, offer: dict) -> dict` — normalises an API add
  payload into a storage row.

### 5.3 `app/db.py` additions

- `add_cart_item(item: dict) -> tuple[int, bool]` — returns `(id, created)`;
  `created=False` when the identity already exists.
- `list_cart() -> list[dict]`
- `get_cart_item(item_id) -> dict | None`
- `update_cart_quantity(item_id, quantity) -> dict | None`
- `update_cart_item(item_id, **fields) -> dict | None` — applies refresh
  results (price, stock, condition, finish, `offer_fetched_at`,
  `last_checked_at`, `status`).
- `remove_cart_item(item_id) -> None`
- `clear_cart() -> None`

The `SCHEMA` string gains the `cart_items` table and its unique index; the
existing `init_schema` applies it idempotently.

## 6. API

| method | route | body / result |
|---|---|---|
| `GET` | `/api/cart` | `{"items": [...], "count": N}` |
| `POST` | `/api/cart` | `{"card": {key, name, set_code, collector_number, quantity}, "offer": {...}}` → `{"id", "created"}` |
| `PATCH` | `/api/cart/{id}` | `{"quantity": N}` → updated item |
| `PATCH` | `/api/cart/{id}/refresh` | snapshot fields from a re-check → updated item |
| `DELETE` | `/api/cart/{id}` | `{"ok": true}` |
| `DELETE` | `/api/cart` | clear all → `{"ok": true}` |
| `POST` | `/api/cart/refresh` | → `{"run_id", "card_count", "store_ids", "skipped_store_ids"}` |

Validation:

- `quantity` is clamped to an integer `>= 1`.
- `POST /api/cart/refresh` with an empty cart → `400`.
- `POST /api/cart/refresh` when no cart store is enabled in config → `409`.

## 7. Refresh flow

Client-driven reconciliation, reusing the existing run/SSE machinery:

1. **Server** (`POST /api/cart/refresh`):
   - Load the cart and dedupe to distinct `card_key`s.
   - Rebuild a `CardRequest` per distinct card from its stored name / set code /
     collector number (quantity carried along; irrelevant to search).
   - Collect the distinct `store_id`s, filter to configs that are `enabled`,
     and build adapters.
   - Create an orchestrator run with `force_refresh=True` — an explicit re-check
     should bypass the 6-hour cache.
   - Return `{run_id, card_count, store_ids, skipped_store_ids}`.
2. **Client** subscribes to the existing `GET /api/runs/{run_id}/stream`.
3. On each `result` event, for every cart line matching that `card_key` +
   `store_id`:
   - Find the offer whose `identity_key` matches the line.
   - **Found:** `PATCH /api/cart/{id}/refresh` with the fresh price, condition,
     finish, stock, `offer_fetched_at`, `last_checked_at`, `status="ok"`.
     Highlight the line when the price moved.
   - **Not found:** `PATCH` the line to `status="unavailable"` and
     `last_checked_at`; keep the old price and the line itself.
4. On `done`, re-render the drawer and toast a summary, e.g.
   *"12 lines re-checked · 3 prices changed · 1 no longer listed"*.
5. Lines whose store is not enabled (`skipped_store_ids`) are left untouched and
   named in the toast.

## 8. UI

### 8.1 Aesthetic direction

Extends the existing "Cartomancer's Ledger" system without importing a new
aesthetic: Cinzel display headings, Hanken Grotesk UI text, JetBrains Mono for
numerals, ink-and-gold palette (`--gold`, `--line`, `--panel`), film grain, dark
backdrop. The cart is a page of the same ledger.

### 8.2 Top-bar trigger

A satchel sigil button to the right of the status pill with a small gold seal
badge showing the line count (hidden when zero). Opens the drawer.

### 8.3 The Satchel drawer

A right-anchored modal `<dialog>` (native focus trapping, `Esc`, and backdrop
handling):

- **Header:** eyebrow "The Satchel", item count, close control.
- **Alerts strip:** shown when there are duplicate-choice cards or unavailable
  lines.
- **Store sections:** store name + colour dot + per-store subtotal; line items
  beneath.
- **Line item:** thumbnail (`image_url`, else `#i-stack` placeholder); card name
  and printing; condition/finish tags; `− qty +` stepper; unit price; line
  total; listing link (hidden when the URL is unsafe); remove control.
  Out-of-stock and `unavailable` lines are flagged; a moved price shows
  "was S$X".
- **Footer:** grand total in mono; a note counting lines excluded because they
  are unavailable/out of stock; **Re-check prices** with an inline progress
  state; **Clear satchel** with confirmation.
- **Empty state:** a short nudge back to the hunt.

### 8.4 Add affordances in results

- Each offer row gains a `+` add button.
- Each collapsed card row gains **“Add best”** on its current best (filtered)
  offer.
- A button whose exact offer is already saved renders in an "in satchel" state.
- New sprite symbols: `i-satchel`, `i-plus`, `i-minus`, `i-cart`.

### 8.5 Behaviour & edge cases

- Quantity defaults to the card's list quantity at add time; the stepper clamps
  to `>= 1`; line total = unit price × quantity.
- Exact-duplicate add is a no-op with a toast; different offers for the same
  card are both kept and flagged "duplicate choice".
- Out-of-stock offers can be added; they are flagged and excluded from totals.
- `unavailable` lines keep their last-known price and are excluded from totals.
- Store subtotals and the grand total count only `ok` + in-stock lines.
  Duplicate-choice lines are still counted; the alert strip warns about them.
- Unsafe or absent URLs are stored but their link is not rendered.
- A failed refresh never clears saved data; `unavailable` is set only by a
  successful re-check that did not find the offer.
- The cart is intact after reload or restart.
- Accessibility: native dialog semantics, labelled steppers and buttons, a
  polite live region for totals, and `prefers-reduced-motion` respected for the
  price-change flash.

## 9. Files touched

- `app/db.py` — schema + cart methods.
- `app/cart.py` — **new** domain helpers.
- `app/main.py` — cart routes and refresh.
- `web/index.html`, `web/app.js`, `web/styles.css`.
- `tests/test_db.py`, `tests/test_cart.py` (**new**), `tests/test_api.py`,
  `tests/test_web.py`.
- `README.md` — v1 constraints note that the cart saves offers for manual
  purchase but there is still no checkout automation.

## 10. Testing

- **DB (`tests/test_db.py`):** cart CRUD, unique-identity dedupe, quantity
  update, clear.
- **Helpers (`tests/test_cart.py`):** identity matching, duplicate-choice
  detection, store grouping and subtotals, exclusion of unavailable/out-of-stock
  lines from totals.
- **API (`tests/test_api.py`):** add/list/patch/remove/clear round-trip;
  `POST /api/cart/refresh` returns a run whose `card_count` and `store_ids`
  match the cart, using a fake adapter as the existing run test does.
- **Web assets (`tests/test_web.py`):** the served `index.html` contains the
  satchel trigger and dialog; `app.js` is served.
- No JavaScript test runner exists in the repo; front-end behaviour remains
  covered by served-asset assertions, consistent with the current suite.

## 11. Open questions / assumptions

- The cart refresh re-uses the store adapters as-is; if a cart store is removed
  from `stores.yaml`, its lines simply stop refreshing and are reported as
  skipped.
- Live price movement between "Re-check prices" and completing a manual purchase
  is out of scope; the cart shows the last checked price and time.
