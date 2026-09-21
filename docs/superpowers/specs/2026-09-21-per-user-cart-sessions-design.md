# Per-User Cart Sessions

**Date:** 2026-09-21
**Status:** Approved design, pending implementation plan
**Parent spec:** `docs/superpowers/specs/2026-09-19-mtg-card-tracker-design.md`
**Related:** `docs/superpowers/specs/2026-09-20-shopping-cart-design.md`

## 1. Summary

The Satchel (cart) is currently a single global cart: `cart_items` has no owner,
`identity_key` is unique across the whole table, and every `/api/cart*` route
reads and writes all rows. Any browser that opens the app sees and edits the
same cart, and even shares duplicate-detection: adding an offer twice from two
machines silently merges into one line.

This change gives each anonymous browser its own cart via an HTTP-only session
cookie. A random opaque token is generated on first cart use, stored in
`cart_sessions`, and used as the `owner_id` on every cart line. Idle sessions
and their lines are purged after a configurable TTL so the database does not
grow without bound.

The project intentionally has no accounts (see parent spec non-goals), so this
is anonymous session identity only — not authentication.

## 2. Goals

- Isolate carts per browser: two browsers never see or mutate each other's
  lines.
- Scope duplicate detection to a single cart: `(owner_id, identity_key)` is
  unique, not `identity_key` alone.
- Persist a cart across page reloads and server restarts for the same browser.
- Bound database growth: purge idle sessions and their lines after a
  configurable TTL (default 30 days), using a sliding window so active users
  never lose their cart.
- Keep the existing `/api/cart*` response shapes, so the frontend needs no
  changes.

## 3. Non-goals

- No authentication, login, or user accounts.
- No change to `saved_lists`; saved lists stay global and out of scope.
- No change to cart math, checkout permalinks, or the refresh/fetch flow beyond
  scoping it to one owner.
- No cross-device cart sync: a cart lives only where its cookie lives.
- No background scheduler; purging is lazy and inline.

## 4. Architecture

- **`app/sessions.py`** (new) owns session concerns in isolation:
  - the cookie name and attributes,
  - token generation (`secrets.token_urlsafe(32)`),
  - the `MTG_CART_TTL_DAYS` configuration (parsed once, default `30`),
  - the purge logic (delete expired sessions, then orphaned cart lines).
- **`app/db.py`** owns persistence: a `cart_sessions` table plus owner-scoped
  variants of every existing cart method.
- **`app/main.py`** owns the HTTP seam: a `cart_owner(...)` FastAPI dependency
  declared by every `/api/cart*` route. It resolves the cookie to an owner id,
  creates a session and sets the cookie when needed, bumps `last_seen`, runs
  the lazy purge, and returns the owner id for the route to use.
- **`app/cart.py`** is unchanged except that its helpers already operate on a
  list of lines and are naturally owner-agnostic.
- **Frontend** is unchanged: `web/app.js` uses same-origin `fetch`, so the
  browser sends the cookie automatically.

## 5. Data model

New table:

```sql
CREATE TABLE IF NOT EXISTS cart_sessions (
    id         TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL
);
```

`cart_items` changes:

- Add `owner_id TEXT NOT NULL`.
- Replace the table-level `UNIQUE(identity_key)` with a unique index on
  `(owner_id, identity_key)`.

### Migration

SQLite cannot drop a column-level `UNIQUE` with `ALTER TABLE`, so the migration
is a one-time rebuild. In `Database.init_schema`, after the schema script runs,
check `PRAGMA table_info(cart_items)` for `owner_id`:

- **Absent** (old database): `DROP TABLE cart_items`, recreate it from the new
  schema, and create the unique index. This intentionally discards the legacy
  ownerless rows — the cart is ephemeral, and they could never be shown to any
  browser.
- **Present**: nothing to do (idempotent for every later startup).

Fresh installations create the new shape directly through the normal
`CREATE TABLE IF NOT EXISTS` path.

## 6. Cookie and session handling

- Cookie name `mtg_cart`; value is the session id (an opaque
  `secrets.token_urlsafe(32)` token). The token *is* the primary key, so no
  separate lookup indirection is needed.
- Attributes: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` equal to the TTL
  in seconds, and `Secure` when the incoming request scheme is `https` (the app
  is deployable behind the compose proxy on HTTP or HTTPS).
- The cookie is (re)set on **every** cart request, not only when it is first
  minted. This keeps `Max-Age` sliding in step with the server-side
  `last_seen`, so an active browser's cookie never expires while its session is
  still alive.
- On a cart request, resolve the cookie:
  - valid cookie whose session exists → reuse it, bump `last_seen`, refresh the
    cookie;
  - absent, malformed, or unknown cookie → create a new session and set the
    cookie on the response;
  - expired session (already purged) → same as unknown: fresh empty cart.
- Sessions are created lazily: only `/api/cart*` routes declare the dependency,
  so static files, `/healthz`, stores, runs, and lists never create rows.

## 7. TTL and purge

- `MTG_CART_TTL_DAYS` environment variable, default `30`. Empty, missing, or
  non-numeric values fall back to the default. Converted to seconds once at
  import.
- Sliding window: every surviving cart request refreshes the session's
  `last_seen`, so an actively used cart never expires.
- Purge runs inline at the **start** of each cart request, before the cookie is
  resolved. Order matters: purging first means a session idle longer than the
  TTL is already gone by the time its cookie is read, so that request starts a
  fresh empty cart rather than reviving the stale session. The two statements
  are:
  1. `DELETE FROM cart_sessions WHERE last_seen < now - ttl`.
  2. `DELETE FROM cart_items WHERE owner_id NOT IN (SELECT id FROM cart_sessions)`
     — removes lines of expired sessions and any orphaned rows.
- A session touched within the TTL survives the purge and is then refreshed by
  the same request.
- No background job and no throttle: at this app's scale these are small,
  indexed deletes.

## 8. Endpoints

Every `/api/cart*` route takes the `owner_id` dependency and scopes its work:

| Route | Behavior change |
| --- | --- |
| `GET /api/cart` | Returns only the owner's lines; store checkout URLs computed from those lines. |
| `POST /api/cart` | Dedupes on `(owner_id, identity_key)`; same offer in another cart is a separate line. |
| `PATCH /api/cart/{id}` | Quantity update scoped to owner; 404 if the id belongs to someone else. |
| `DELETE /api/cart/{id}` | Scoped to owner; 404 on mismatch. |
| `PATCH /api/cart/{id}/refresh` | Scoped to owner; 404 on mismatch. |
| `DELETE /api/cart` | Clears only the owner's lines, not the whole table. |
| `POST /api/cart/refresh` | Re-fetches only the owner's lines. |

Response shapes are unchanged, so the Satchel UI and refresh loop keep working
without edits.

## 9. Edge cases and error handling

- **No cookie / tampered token / unknown session** → new empty cart and a new
  cookie; never an error.
- **Guessed `item_id` owned by another session** → `404 Not found`, so carts
  cannot be mutated cross-session.
- **Expired session** → purged; the next request behaves like a first visit.
- **Two browser profiles / private window** → independent carts, by design.
- **Cookie disabled by the client** → each request looks like a new session;
  the cart works for the session but does not persist. Acceptable for an
  anonymous cart.

## 10. Testing

- **`tests/test_db.py`**: per-owner isolation (add for owner A, `list_cart(B)`
  is empty), per-owner uniqueness (same `identity_key` allowed for two owners,
  rejected for the same owner), and purge (expired session's lines and orphaned
  rows are deleted; live session's lines survive).
- **`tests/test_api.py`**: two `TestClient` instances with separate cookie jars
  see separate carts; the first cart call sets `mtg_cart` (and it is `HttpOnly`);
  `PATCH`/`DELETE` on another session's `item_id` returns 404; an idle session
  older than the TTL is dropped on the next cart request.
- Existing `tests/test_cart.py` helpers remain valid; any `Database` calls in
  tests are updated for the new `owner_id` argument.

## 11. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MTG_CART_TTL_DAYS` | `30` | Idle days before a session and its cart are purged. |

Documented alongside the existing `MTG_BYPARR_URL` in `README.md`,
`.env.example`, and `compose.yaml`.
