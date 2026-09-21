from starlette.requests import Request
from starlette.responses import Response

from app.db import Database
from app.sessions import (
    CART_COOKIE,
    CART_TTL_SECONDS,
    DEFAULT_TTL_DAYS,
    _ttl_seconds_from_env,
    new_token,
    resolve_owner,
    set_cart_cookie,
)


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def _request(cookie=None, scheme="http"):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{CART_COOKIE}={cookie}".encode()))
    return Request(
        {"type": "http", "method": "GET", "path": "/api/cart",
         "scheme": scheme, "server": ("testserver", 80), "headers": headers}
    )


def test_token_is_random_and_urlsafe():
    assert new_token() != new_token()
    assert len(new_token()) >= 32


def test_ttl_reads_env_with_default_fallback(monkeypatch):
    monkeypatch.delenv("MTG_CART_TTL_DAYS", raising=False)
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "7")
    assert _ttl_seconds_from_env() == 7 * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "not-a-number")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "0")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "inf")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400
    monkeypatch.setenv("MTG_CART_TTL_DAYS", "nan")
    assert _ttl_seconds_from_env() == DEFAULT_TTL_DAYS * 86400


def test_resolve_owner_mints_session_and_sets_cookie(tmp_path):
    db = _db(tmp_path)
    response = Response()
    owner = resolve_owner(db, _request(), response, CART_TTL_SECONDS)
    assert db.touch_session(owner) is True
    header = response.headers["set-cookie"]
    assert f"{CART_COOKIE}={owner}" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    db.close()


def test_resolve_owner_reuses_known_cookie(tmp_path):
    db = _db(tmp_path)
    first = resolve_owner(db, _request(), Response(), CART_TTL_SECONDS)
    again = resolve_owner(db, _request(cookie=first), Response(), CART_TTL_SECONDS)
    assert again == first
    db.close()


def test_resolve_owner_replaces_expired_cookie(tmp_path):
    db = _db(tmp_path)
    old = resolve_owner(db, _request(), Response(), CART_TTL_SECONDS)
    db._conn.execute("UPDATE cart_sessions SET last_seen=1.0 WHERE id=?", (old,))
    db._conn.commit()

    response = Response()
    new = resolve_owner(db, _request(cookie=old), response, 60)
    assert new != old
    assert db.touch_session(old) is False
    assert f"{CART_COOKIE}={new}" in response.headers["set-cookie"]
    db.close()


def test_set_cart_cookie_secure_flag():
    response = Response()
    set_cart_cookie(response, "tok", 100, secure=True)
    assert "Secure" in response.headers["set-cookie"]


def test_resolve_owner_secure_cookie_follows_request_scheme(tmp_path):
    db = _db(tmp_path)

    https_response = Response()
    resolve_owner(db, _request(scheme="https"), https_response, CART_TTL_SECONDS)
    assert "Secure" in https_response.headers["set-cookie"]

    http_response = Response()
    resolve_owner(db, _request(scheme="http"), http_response, CART_TTL_SECONDS)
    assert "Secure" not in http_response.headers["set-cookie"]
    db.close()
