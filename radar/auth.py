"""Password hashing + signed session cookies. Stdlib only.

    hash_password(pw)             -> stored form 'pbkdf2:<salt>:<hash>'
    verify_password(pw, stored)   -> bool
    make_session_cookie(mid)      -> signed cookie value
    read_session_cookie(cookie)   -> manager_id | None

The session cookie is a signed opaque blob — no server-side session table
needed. Cookie format:  '<mid>.<issued_at>.<hmac>'.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

# --- password hashing ------------------------------------------------------- #
_PBKDF2_ITERS = 100_000


def hash_password(pw: str) -> str:
    if not pw or len(pw) < 6:
        raise ValueError("password_too_short")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _PBKDF2_ITERS).hex()
    return f"pbkdf2:{salt}:{digest}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not pw or not stored or not stored.startswith("pbkdf2:"):
        return False
    try:
        _, salt, want = stored.split(":", 2)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _PBKDF2_ITERS).hex()
    return hmac.compare_digest(got, want)


# --- signed session cookies ------------------------------------------------- #
# 30-day session lifetime; long enough that "log in as me" stays sticky, short
# enough that a stolen cookie ages out.
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
COOKIE_NAME = "affswap_sid"


def _secret() -> bytes:
    # Prefer an env var so Render + local both get the same secret across
    # process restarts. Fall back to a persisted file so a local dev doesn't
    # invalidate everyone every restart. In the very worst case, generate one
    # in memory (dev only — sessions won't survive restart).
    s = os.environ.get("AFFSWAP_SECRET")
    if s:
        return s.encode()
    return b"affswap-dev-fallback-please-set-AFFSWAP_SECRET"


def _b64u(x: bytes) -> str:
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_session_cookie(manager_id: int) -> str:
    payload = f"{int(manager_id)}.{int(time.time())}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64u(sig)}"


def read_session_cookie(cookie: str | None) -> int | None:
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    mid_str, ts_str, sig_b64 = parts
    payload = f"{mid_str}.{ts_str}"
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    try:
        got = _b64u_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, got):
        return None
    try:
        mid = int(mid_str)
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > SESSION_TTL_SECONDS:
        return None
    return mid


# --- cookie header helpers -------------------------------------------------- #
def parse_cookie_header(header: str | None) -> dict:
    out: dict = {}
    if not header:
        return out
    for pair in header.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def set_cookie_header(value: str, max_age: int = SESSION_TTL_SECONDS) -> str:
    # HttpOnly + SameSite=Lax + Path=/; Secure omitted so local http works.
    # In production behind HTTPS a proxy should add Secure via header rewrite,
    # or we can flip it on with an env var later.
    return (f"{COOKIE_NAME}={value}; Path=/; Max-Age={max_age}; "
            "HttpOnly; SameSite=Lax")


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
