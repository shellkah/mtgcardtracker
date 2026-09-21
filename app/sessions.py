from __future__ import annotations

import math
import os
import secrets
import time

from starlette.requests import Request
from starlette.responses import Response

from app.db import Database

CART_COOKIE = "mtg_cart"
DEFAULT_TTL_DAYS = 30


def _ttl_seconds_from_env() -> float:
    raw = os.environ.get("MTG_CART_TTL_DAYS", "").strip()
    try:
        days = float(raw) if raw else float(DEFAULT_TTL_DAYS)
    except ValueError:
        days = float(DEFAULT_TTL_DAYS)
    if not math.isfinite(days) or days <= 0:
        days = float(DEFAULT_TTL_DAYS)
    return days * 86400


#: Idle lifetime of an anonymous cart session. Read once at import; tests can
#: monkeypatch this attribute to exercise expiry.
CART_TTL_SECONDS: float = _ttl_seconds_from_env()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def set_cart_cookie(
    response: Response, owner_id: str, ttl_seconds: float, secure: bool
) -> None:
    response.set_cookie(
        key=CART_COOKIE,
        value=owner_id,
        max_age=int(ttl_seconds),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def resolve_owner(
    db: Database, request: Request, response: Response, ttl_seconds: float
) -> str:
    """Return the cart owner for this request, minting a session if needed.

    Expired sessions are purged first, so a cookie whose session sat idle past
    the TTL starts a fresh empty cart instead of being revived.
    """
    db.purge_expired_sessions(time.time() - ttl_seconds)
    token = request.cookies.get(CART_COOKIE)
    if token and db.touch_session(token):
        owner_id = token
    else:
        owner_id = new_token()
        db.create_session(owner_id)
    set_cart_cookie(response, owner_id, ttl_seconds, request.url.scheme == "https")
    return owner_id
