"""Password hashing + signed session cookies + LinkedIn OAuth. Stdlib only.

    hash_password(pw)             -> stored form 'pbkdf2:<salt>:<hash>'
    verify_password(pw, stored)   -> bool
    make_session_cookie(mid)      -> signed cookie value
    read_session_cookie(cookie)   -> manager_id | None
    linkedin_start_url(host)      -> LinkedIn authorize URL + state token
    linkedin_exchange(code, host) -> LinkedIn userinfo dict

The session cookie is a signed opaque blob — no server-side session table
needed. Cookie format:  '<mid>.<issued_at>.<hmac>'.

LinkedIn OAuth uses OpenID Connect via 'openid profile email' scope, so we
get a well-formed userinfo response with sub / email / email_verified /
name / picture without any extra API calls.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

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


# --- LinkedIn OAuth --------------------------------------------------------- #
LINKEDIN_AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO = "https://api.linkedin.com/v2/userinfo"
LI_STATE_COOKIE = "affswap_li_state"
LI_STATE_TTL = 10 * 60  # 10 min to complete the redirect dance


def linkedin_configured() -> bool:
    return bool(os.environ.get("LINKEDIN_CLIENT_ID") and
                os.environ.get("LINKEDIN_CLIENT_SECRET"))


def linkedin_redirect_uri(host: str) -> str:
    """Derive the redirect URL. Override with LINKEDIN_REDIRECT_URI when the
    host header can't be trusted (Render sets X-Forwarded-Host)."""
    override = os.environ.get("LINKEDIN_REDIRECT_URI")
    if override:
        return override
    scheme = "https" if not host.startswith(("localhost", "127.")) else "http"
    return f"{scheme}://{host}/api/oauth/linkedin/callback"


def linkedin_start(host: str) -> tuple[str, str]:
    """Return (authorize_url, state). Caller sets state as a short-lived cookie."""
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": os.environ.get("LINKEDIN_CLIENT_ID", ""),
        "redirect_uri": linkedin_redirect_uri(host),
        "scope": "openid profile email",
        "state": state,
    }
    url = LINKEDIN_AUTHORIZE + "?" + urllib.parse.urlencode(params)
    return url, state


def linkedin_state_cookie(state: str) -> str:
    return (f"{LI_STATE_COOKIE}={state}; Path=/api/oauth/linkedin; "
            f"Max-Age={LI_STATE_TTL}; HttpOnly; SameSite=Lax")


def linkedin_clear_state_cookie() -> str:
    return f"{LI_STATE_COOKIE}=; Path=/api/oauth/linkedin; Max-Age=0; HttpOnly; SameSite=Lax"


def linkedin_exchange(code: str, host: str) -> dict:
    """Exchange the authorization code for an access token, then fetch the
    OIDC userinfo endpoint. Returns the userinfo dict (sub/email/name/etc.)
    or raises RuntimeError with a compact error message on failure."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": linkedin_redirect_uri(host),
        "client_id": os.environ.get("LINKEDIN_CLIENT_ID", ""),
        "client_secret": os.environ.get("LINKEDIN_CLIENT_SECRET", ""),
    }).encode()
    req = urllib.request.Request(
        LINKEDIN_TOKEN, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            token_resp = json.loads(r.read().decode())
    except Exception as e:
        raise RuntimeError(f"token_exchange_failed: {e}") from e
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError("no_access_token")
    ui_req = urllib.request.Request(
        LINKEDIN_USERINFO,
        headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(ui_req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        raise RuntimeError(f"userinfo_failed: {e}") from e
