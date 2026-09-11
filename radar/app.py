"""Member web app — the clickable Affswap front end (stdlib http.server only).

Serves a single-page app (vanilla JS, no build step) in the locked pale-blue
design, backed by JSON endpoints that wrap radar/views.py and the swap / review
/ loyalty services. This is the MEMBER surface (distinct from the back office).

    python3 -m radar.app            # http://127.0.0.1:8766

For the demo it acts as one signed-in verified member ("You"); real auth is a
later step. Actions (add review, want/have, loyalty) hit the same service layer
the back office and API use.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth, config, reviews, swaps, views
from .db import connect, now_iso
from .locations import flag, name

DEMO_UID = "app_demo_user"


def _ensure_demo(conn) -> int:
    """The fallback demo member — anyone who's not logged in sees this account.
    Kept for pages the marketing site links to (home, markets) so a fresh
    visitor still sees a signed-in-looking app."""
    row = conn.execute("SELECT id FROM chat_managers WHERE cc_uid=?", (DEMO_UID,)).fetchone()
    if row:
        swaps.ensure_account(conn, row["id"])
        return row["id"]
    cur = conn.execute(
        "INSERT INTO chat_managers (cc_uid, handle, sector, status, created_at) "
        "VALUES (?,?,?, 'verified', ?)", (DEMO_UID, "You", "casino", now_iso()))
    mid = cur.lastrowid
    swaps.ensure_account(conn, mid, plan="standard")
    conn.commit()
    return mid


def _me_from_session(conn, cookies) -> int | None:
    """Resolve the current member from the signed session cookie, or None."""
    cookie = cookies.get(auth.COOKIE_NAME)
    mid = auth.read_session_cookie(cookie)
    if mid is None:
        return None
    # Make sure the row actually exists (a stale cookie for a deleted user is None).
    row = conn.execute("SELECT id FROM chat_managers WHERE id=?", (mid,)).fetchone()
    if not row:
        return None
    swaps.ensure_account(conn, mid)
    return mid


def _me(conn, cookies=None) -> int:
    """Current member id: session cookie if present and valid, else the demo."""
    if cookies is not None:
        mid = _me_from_session(conn, cookies)
        if mid is not None:
            return mid
    return _ensure_demo(conn)


def _shot_url(image_ref, domain=None):
    """Homepage image URL for a profile.

    * an embedded upload (data: URI) is used as-is — it travels with the DB, so
      it works on any host;
    * otherwise we render the homepage LIVE, on demand, via thum.io keyed by the
      domain — no files to store or deploy, and it stays current;
    * a stored local file path (dev only) falls back to /shot/<name>.
    """
    if image_ref and str(image_ref).startswith("data:"):
        return image_ref
    if domain:
        # mShots (WordPress.com) renders homepages on demand and, unlike thum.io's
        # free tier, permits hot-linking as an <img>. It renders async: the first
        # request may return a "generating" placeholder, so the client retries.
        return ("https://s0.wp.com/mshots/v1/"
                + urllib.parse.quote("https://" + domain + "/", safe="") + "?w=1200")
    if image_ref:
        return "/shot/" + os.path.basename(image_ref)
    return None


# --------------------------------------------------------------------------- #
# JSON endpoint builders
# --------------------------------------------------------------------------- #
def api_home(conn):
    idx = views.countries_index(conn)
    websites = conn.execute(
        "SELECT COUNT(*) c FROM sites WHERE classification='affiliate'").fetchone()["c"]
    # websites + countries are REAL live DB counts. managers_joined / swaps_to_date
    # / online are representative launch figures for the demo — replace with real
    # analytics + presence once the network is live.
    stats = {"managers_joined": 340, "websites": websites,
             "swaps_to_date": 1240, "online": 48}
    return {"markets": sorted(idx["countries"], key=lambda c: -c["count"])[:8],
            "total_markets": idx["markets"], "stats": stats}


def _fmt_traffic(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n/1000)}K"
    return str(n)


def api_markets(conn):
    idx = views.countries_index(conn)
    countries = idx.get("countries", []) or []
    verts_by_country: dict = {}
    for r in conn.execute("""
        SELECT rg.country AS iso, sv.vertical
          FROM sites s
          JOIN site_regions rg ON rg.site_id = s.id
          JOIN site_verticals sv ON sv.site_id = s.id
         WHERE s.classification='affiliate'
         GROUP BY rg.country, sv.vertical
    """):
        verts_by_country.setdefault(r["iso"], []).append(r["vertical"])
    for c in countries:
        c["etv"] = c.get("total_etv") or 0
        c["tf"] = _fmt_traffic(c["etv"])
        c["verts"] = verts_by_country.get(c["iso"], [])
    return {"markets": idx.get("markets", len(countries)), "countries": countries}


def api_account(conn, mid):
    row = conn.execute(
        "SELECT handle, real_name, company, work_email, linkedin_url, avatar_url, "
        "site_url, sector, status, linkedin_verified, created_at, last_login_at "
        "FROM chat_managers WHERE id=?", (mid,)
    ).fetchone()
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


def api_account_update(conn, mid, payload):
    """Member-editable profile fields. Only the ones a user should be able to
    change themselves — not handle, work_email, status, linkedin_verified,
    or created_at."""
    editable = {
        "real_name": (payload.get("real_name") or "").strip() or None,
        "company":   (payload.get("company") or "").strip() or None,
        "linkedin_url": (payload.get("linkedin_url") or "").strip() or None,
        "site_url":  (payload.get("site_url") or "").strip() or None,
        "sector":    (payload.get("sector") or "").strip() or None,
    }
    sets = ", ".join(f"{k}=?" for k in editable)
    conn.execute(
        f"UPDATE chat_managers SET {sets} WHERE id=?",
        (*editable.values(), mid))
    conn.commit()
    return {"ok": True}


def api_pricing(conn):
    """Live pricing for the Payments page. Returns the Unlimited
    early-bird status: how many founder seats are taken vs. total,
    and which price the next signup would pay. Also returns the
    Standard / Pro prices so the frontend has one source of truth."""
    taken = conn.execute(
        "SELECT COUNT(*) c FROM swap_accounts WHERE plan='unlimited'").fetchone()["c"]
    total = int(config.UNLIMITED_EARLYBIRD_SEATS)
    seats_left = max(0, total - int(taken))
    is_early = seats_left > 0
    return {
        "standard":  {"price": config.SWAP_PLANS["standard"]["price"]},
        "pro":       {"price": config.SWAP_PLANS["pro"]["price"]},
        "unlimited": {
            "price": (config.UNLIMITED_EARLYBIRD_PRICE if is_early
                      else config.UNLIMITED_STANDARD_PRICE),
            "standard_price": config.UNLIMITED_STANDARD_PRICE,
            "earlybird_price": config.UNLIMITED_EARLYBIRD_PRICE,
            "is_earlybird":    is_early,
            "seats_left":      seats_left,
            "seats_total":     total,
            "seats_taken":     int(taken),
        },
        "swap_topup": {"unit_price": config.SWAP_TOPUP_PRICE},
    }


def api_swaps(conn, mid):
    full = swaps.ledger(conn)
    mine = [e for e in full if e.get("from_id") == mid or e.get("to_id") == mid]
    return {
        "matches": swaps.list_matches(conn, mid),
        "ledger": mine,
        "access": swaps.access(conn, mid),
    }


SHARE_COOLDOWN_DAYS = 7
SHARE_REWARD_SWAPS = 1


def api_share_status(conn, mid):
    """When was the last share, and can this member claim now?"""
    row = conn.execute(
        "SELECT created_at FROM share_events WHERE manager_id=? "
        "ORDER BY created_at DESC LIMIT 1", (mid,)).fetchone()
    total = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(reward_swaps),0) s FROM share_events WHERE manager_id=?",
        (mid,)).fetchone()
    from datetime import datetime, timezone, timedelta
    can_claim = True
    next_claim_at = None
    if row and row["created_at"]:
        try:
            last = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            eligible = last + timedelta(days=SHARE_COOLDOWN_DAYS)
            can_claim = datetime.now(timezone.utc) >= eligible
            if not can_claim:
                next_claim_at = eligible.isoformat()
        except Exception:
            pass
    return {
        "can_claim": can_claim,
        "next_claim_at": next_claim_at,
        "total_shares": total["c"] or 0,
        "total_swaps_earned": total["s"] or 0,
        "cooldown_days": SHARE_COOLDOWN_DAYS,
        "reward_swaps": SHARE_REWARD_SWAPS,
    }


def api_share_claim(conn, mid):
    """Honor-system claim: user says they shared to LinkedIn; we grant a
    bonus swap if they're past the cooldown. Real verification (posting
    via LinkedIn API) would need the Marketing Developer Platform
    partnership — deferred; this is the growth-loop MVP."""
    status = api_share_status(conn, mid)
    if not status["can_claim"]:
        return {"ok": False, "error": "cooldown_active",
                "next_claim_at": status["next_claim_at"]}
    conn.execute(
        "INSERT INTO share_events (manager_id, kind, reward_swaps, created_at) "
        "VALUES (?, 'linkedin', ?, ?)",
        (mid, SHARE_REWARD_SWAPS, now_iso()))
    swaps.grant_swaps(conn, mid, SHARE_REWARD_SWAPS)
    conn.commit()
    return {"ok": True, "granted": SHARE_REWARD_SWAPS}


# --- Chat -------------------------------------------------------------------
CHAT_ROOMS = {
    "global": {"key": "global", "name": "Global lobby",
               "desc": "Every verified manager on Affswap."},
    # More rooms can go here later (per-country / per-vertical).
}
CHAT_MAX_BODY = 500                 # chars per message
CHAT_MAX_PER_MINUTE = 20            # posts per member per minute (soft rate cap)
CHAT_DEFAULT_LIMIT = 60             # initial history fetched


def _chat_room(room: str | None) -> str:
    return room if room in CHAT_ROOMS else "global"


def api_chat_list(conn, mid, room: str, since_id: int, limit: int):
    """Return messages in a room. `since_id > 0` means give me only rows
    newer than this — the poll path. `since_id == 0` means initial load —
    return the most recent `limit` in chronological order."""
    room = _chat_room(room)
    limit = max(1, min(int(limit or CHAT_DEFAULT_LIMIT), 200))
    if since_id > 0:
        rows = conn.execute(
            "SELECT m.id, m.body, m.created_at, m.manager_id, "
            "       c.handle, c.real_name, c.avatar_url "
            "FROM chat_messages m JOIN chat_managers c ON c.id=m.manager_id "
            "WHERE m.room=? AND m.id > ? ORDER BY m.id ASC LIMIT ?",
            (room, since_id, limit)).fetchall()
    else:
        # Newest N, then flip so oldest-first for rendering
        rows = list(conn.execute(
            "SELECT m.id, m.body, m.created_at, m.manager_id, "
            "       c.handle, c.real_name, c.avatar_url "
            "FROM chat_messages m JOIN chat_managers c ON c.id=m.manager_id "
            "WHERE m.room=? ORDER BY m.id DESC LIMIT ?",
            (room, limit)).fetchall())[::-1]
    messages = [{
        "id": r["id"], "body": r["body"], "at": r["created_at"],
        "manager_id": r["manager_id"],
        "handle": r["handle"], "real_name": r["real_name"],
        "avatar_url": r["avatar_url"],
        "is_you": r["manager_id"] == mid,
    } for r in rows]
    return {"room": room, "messages": messages,
            "last_id": messages[-1]["id"] if messages else since_id}


def api_chat_send(conn, mid, payload):
    """Post a message. Requires an authenticated caller (route enforces).
    Soft rate-cap so a runaway client can't spam."""
    body = (payload.get("body") or "").strip()
    room = _chat_room(payload.get("room"))
    if not body:
        return {"ok": False, "error": "empty_message"}, 400
    if len(body) > CHAT_MAX_BODY:
        return {"ok": False, "error": "too_long",
                "limit": CHAT_MAX_BODY}, 400
    # Rate cap — count posts by this member in the last 60 seconds.
    recent = conn.execute(
        "SELECT COUNT(*) c FROM chat_messages "
        "WHERE manager_id=? AND created_at > datetime('now', '-60 seconds')",
        (mid,)).fetchone()["c"]
    if recent >= CHAT_MAX_PER_MINUTE:
        return {"ok": False, "error": "rate_limited",
                "retry_after": 60}, 429
    when = now_iso()
    cur = conn.execute(
        "INSERT INTO chat_messages (room, manager_id, body, created_at) "
        "VALUES (?,?,?,?)", (room, mid, body, when))
    conn.commit()
    row = conn.execute(
        "SELECT c.handle, c.real_name, c.avatar_url "
        "FROM chat_managers c WHERE c.id=?", (mid,)).fetchone()
    return {
        "ok": True,
        "message": {
            "id": cur.lastrowid, "body": body, "at": when,
            "manager_id": mid,
            "handle": row["handle"] if row else "you",
            "real_name": row["real_name"] if row else None,
            "avatar_url": row["avatar_url"] if row else None,
            "is_you": True,
        },
    }, 200


def api_chat_rooms(conn):
    """List rooms with a live message count for each — small enough to
    include in a single response and lets the UI show '32 messages'."""
    counts = {r["room"]: r["c"] for r in conn.execute(
        "SELECT room, COUNT(*) c FROM chat_messages GROUP BY room")}
    return {"rooms": [{
        "key": k, "name": v["name"], "desc": v["desc"],
        "message_count": counts.get(k, 0),
    } for k, v in CHAT_ROOMS.items()]}


def api_lists(conn, mid):
    """The current member's Want and Have lists, with site info attached."""
    def _fetch(table):
        rows = conn.execute(
            f"""SELECT s.domain, s.display_name, s.etv, s.top_country
                  FROM {table} l
                  JOIN sites s ON s.id = l.site_id
                 WHERE l.manager_id = ?
                 ORDER BY s.etv DESC""", (mid,)).fetchall()
        out = []
        for r in rows:
            iso = r["top_country"] or ""
            out.append({
                "domain": r["domain"],
                "name": r["display_name"] or r["domain"].split(".")[0].capitalize(),
                "etv": r["etv"] or 0,
                "iso": iso,
                "flag": flag(iso) if iso else "🏳️",
            })
        return out
    return {"wants": _fetch("swap_wants"), "haves": _fetch("swap_haves")}


def api_signup(conn, payload):
    handle = (payload.get("handle") or "").strip()
    if not handle:
        return {"ok": False, "error": "handle_required"}
    company = (payload.get("company") or "").strip() or None
    work_email = (payload.get("work_email") or "").strip() or None
    linkedin = (payload.get("linkedin_url") or "").strip() or None
    site_url = (payload.get("site_url") or "").strip() or None
    sector = (payload.get("sector") or "casino").strip() or "casino"
    real_name = (payload.get("real_name") or "").strip() or None
    when = now_iso()
    cur = conn.execute(
        "INSERT INTO chat_managers (cc_uid, handle, real_name, company, work_email, linkedin_url, site_url, sector, status, applied_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
        (f"web_{when}_{handle[:20]}", handle, real_name, company, work_email, linkedin, site_url, sector, when, when)
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "status": "pending"}


def api_register(conn, payload):
    """Full registration with password. On success: creates a verified member,
    logs them in immediately, returns the session cookie header.

    Returns (json_body, cookie_header_or_None).
    """
    email = (payload.get("work_email") or payload.get("email") or "").strip().lower()
    pw = payload.get("password") or ""
    handle = (payload.get("handle") or "").strip()
    if not email or "@" not in email:
        return {"ok": False, "error": "valid_email_required"}, None
    if not handle:
        return {"ok": False, "error": "handle_required"}, None
    if len(pw) < 6:
        return {"ok": False, "error": "password_min_6_chars"}, None
    existing = conn.execute(
        "SELECT id, password_hash FROM chat_managers WHERE lower(work_email)=?",
        (email,)).fetchone()
    if existing and existing["password_hash"]:
        return {"ok": False, "error": "email_already_registered"}, None
    ph = auth.hash_password(pw)
    when = now_iso()
    if existing:
        # Someone previously applied without a password — attach one now.
        conn.execute(
            "UPDATE chat_managers SET password_hash=?, status='verified', "
            "approved_at=?, last_login_at=? WHERE id=?",
            (ph, when, when, existing["id"]))
        mid = existing["id"]
    else:
        real_name = (payload.get("real_name") or "").strip() or None
        company = (payload.get("company") or "").strip() or None
        linkedin = (payload.get("linkedin_url") or "").strip() or None
        site_url = (payload.get("site_url") or "").strip() or None
        sector = (payload.get("sector") or "casino").strip() or "casino"
        cur = conn.execute(
            "INSERT INTO chat_managers "
            "(cc_uid, handle, real_name, company, work_email, linkedin_url, site_url, "
            " sector, password_hash, status, applied_at, approved_at, last_login_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'verified', ?,?,?,?)",
            (f"web_{when}_{handle[:20]}", handle, real_name, company, email, linkedin, site_url,
             sector, ph, when, when, when, when))
        mid = cur.lastrowid
    swaps.ensure_account(conn, mid, plan="standard")
    conn.commit()
    return ({"ok": True, "id": mid, "handle": handle},
            auth.set_cookie_header(auth.make_session_cookie(mid)))


def linkedin_land(conn, ui: dict) -> int:
    """Land a LinkedIn userinfo response into chat_managers:
      - existing linkedin_sub  -> that row (return visit)
      - existing work_email    -> attach linkedin_sub + linkedin_verified
      - else                   -> create new verified member
    Returns the manager_id. Commits.

    Note: LinkedIn's OpenID Connect scope returns sub, name, given_name,
    family_name, email, email_verified, picture, locale — nothing else.
    Job title, current employer, position dates etc. require the
    Marketing Developer Platform partnership, which we don't have. Members
    fill those in manually on the Account view.
    """
    sub = (ui.get("sub") or "").strip()
    email = (ui.get("email") or "").strip().lower()
    name = (ui.get("name") or "").strip()
    given = (ui.get("given_name") or "").strip()
    family = (ui.get("family_name") or "").strip()
    picture = (ui.get("picture") or "").strip() or None
    email_verified = 1 if ui.get("email_verified") else 0
    when = now_iso()
    if not sub:
        raise ValueError("missing_linkedin_sub")

    # 1. Existing LinkedIn identity — refresh picture, name and email in
    #    case they changed on LinkedIn since last sign-in. COALESCE keeps
    #    the existing value when LinkedIn returns null for a field, and
    #    NULLIF turns any empty string LinkedIn returns into NULL so
    #    COALESCE picks up the DB copy instead of blanking it out.
    #    Handle is only auto-refreshed when the stored handle is a
    #    placeholder ("You", "member", empty) so members keep the
    #    display name they've chosen.
    row = conn.execute(
        "SELECT id, handle FROM chat_managers WHERE linkedin_sub=?", (sub,)).fetchone()
    if row:
        placeholder_handles = {"", "you", "member", "user"}
        current_handle = (row["handle"] or "").strip().lower()
        new_handle = given or (name.split(" ")[0] if name else "")
        set_handle = new_handle if current_handle in placeholder_handles else None
        conn.execute(
            "UPDATE chat_managers SET last_login_at=?, "
            "avatar_url=COALESCE(NULLIF(?, ''), avatar_url), "
            "real_name=COALESCE(NULLIF(?, ''), real_name), "
            "work_email=COALESCE(NULLIF(?, ''), work_email), "
            "work_email_confirmed=CASE WHEN ?=1 THEN 1 ELSE work_email_confirmed END, "
            "handle=COALESCE(NULLIF(?, ''), handle), "
            "linkedin_verified=1 "
            "WHERE id=?",
            (when, picture or "", name or "", email or "",
             email_verified, set_handle or "", row["id"]))
        conn.commit()
        return row["id"]

    # 2. Existing email — attach the LinkedIn identity, backfill picture.
    if email:
        row = conn.execute(
            "SELECT id FROM chat_managers WHERE lower(work_email)=?",
            (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE chat_managers SET linkedin_sub=?, linkedin_verified=1, "
                "work_email_confirmed=?, avatar_url=COALESCE(?, avatar_url), "
                "status='verified', approved_at=?, last_login_at=? WHERE id=?",
                (sub, email_verified, picture, when, when, row["id"]))
            swaps.ensure_account(conn, row["id"])
            conn.commit()
            return row["id"]

    # 3. New member.
    handle = given or (name.split(" ")[0] if name else "member")
    cur = conn.execute(
        "INSERT INTO chat_managers "
        "(cc_uid, handle, real_name, work_email, linkedin_sub, avatar_url, "
        " linkedin_verified, work_email_confirmed, sector, status, "
        " applied_at, approved_at, last_login_at, created_at) "
        "VALUES (?,?,?,?,?,?, 1,?, 'casino', 'verified', ?,?,?,?)",
        (f"li_{sub[:24]}", handle, name or None, email or None, sub, picture,
         email_verified, when, when, when, when))
    mid = cur.lastrowid
    swaps.ensure_account(conn, mid, plan="standard")
    conn.commit()
    return mid


def api_login(conn, payload):
    """Email + password login. Returns (json_body, cookie_header_or_None)."""
    email = (payload.get("work_email") or payload.get("email") or "").strip().lower()
    pw = payload.get("password") or ""
    if not email or not pw:
        return {"ok": False, "error": "email_and_password_required"}, None
    row = conn.execute(
        "SELECT id, handle, password_hash, status FROM chat_managers "
        "WHERE lower(work_email)=?", (email,)).fetchone()
    if not row or not row["password_hash"]:
        return {"ok": False, "error": "invalid_credentials"}, None
    if not auth.verify_password(pw, row["password_hash"]):
        return {"ok": False, "error": "invalid_credentials"}, None
    if row["status"] == "banned":
        return {"ok": False, "error": "account_banned"}, None
    conn.execute("UPDATE chat_managers SET last_login_at=? WHERE id=?",
                 (now_iso(), row["id"]))
    swaps.ensure_account(conn, row["id"])
    conn.commit()
    return ({"ok": True, "id": row["id"], "handle": row["handle"]},
            auth.set_cookie_header(auth.make_session_cookie(row["id"])))


def api_geo(conn, iso, vertical, mid):
    d = views.country_list(conn, iso, vertical=vertical or None, sort="traffic")
    cards = d.get("cards") or []
    domains = [c["domain"] for c in cards if c.get("domain")]
    if domains:
        placeholders = ",".join(["?"] * len(domains))
        dom_to_sid = {r["domain"]: r["id"] for r in conn.execute(
            f"SELECT id, domain FROM sites WHERE domain IN ({placeholders})", domains)}
        wants = {r["site_id"] for r in conn.execute(
            "SELECT site_id FROM swap_wants WHERE manager_id=?", (mid,))}
        haves = {r["site_id"] for r in conn.execute(
            "SELECT site_id FROM swap_haves WHERE manager_id=?", (mid,))}
        for c in cards:
            sid = dom_to_sid.get(c.get("domain"))
            c["you_want"] = bool(sid and sid in wants)
            c["you_have"] = bool(sid and sid in haves)
    return d


def api_site(conn, domain, mid):
    p = views.site_profile(conn, domain, published_only=False)
    if not p:
        return None
    p["screenshot"] = _shot_url(p.get("screenshot"), p.get("domain"))
    sid = conn.execute("SELECT id FROM sites WHERE domain=?", (domain,)).fetchone()
    sid = sid["id"] if sid else None
    p["you_reviewed"] = bool(sid and conn.execute(
        "SELECT 1 FROM reviews WHERE manager_id=? AND site_id=? AND status IN ('pending','approved')",
        (mid, sid)).fetchone())
    p["you_want"] = bool(sid and conn.execute(
        "SELECT 1 FROM swap_wants WHERE manager_id=? AND site_id=?", (mid, sid)).fetchone())
    p["you_have"] = bool(sid and conn.execute(
        "SELECT 1 FROM swap_haves WHERE manager_id=? AND site_id=?", (mid, sid)).fetchone())
    p["recent_reviews"] = [
        {"handle": r["handle"], "rating": r["rating"], "body": r["body"]}
        for r in conn.execute(
            "SELECT cm.handle, r.rating, r.body FROM reviews r JOIN chat_managers cm ON cm.id=r.manager_id "
            "WHERE r.site_id=? AND r.status='approved' ORDER BY r.resolved_at DESC LIMIT 4", (sid,))
    ] if sid else []
    return p


PLANS = [{"key": k, **v} for k, v in config.SWAP_PLANS.items()]


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cookies(self) -> dict:
        return auth.parse_cookie_header(self.headers.get("Cookie"))

    def _json(self, obj, code=200, set_cookie: str | None = None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, location: str, cookies: list | None = None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()

    def _forwarded_host(self) -> str:
        # Behind serve.py's reverse proxy, the real host is in X-Forwarded-Host.
        return (self.headers.get("X-Forwarded-Host")
                or self.headers.get("Host", "")).split(",", 1)[0].strip()

    def _html(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": (qs.get(k, [d])[0])
        if u.path == "/":
            return self._html(APP_HTML)
        # LinkedIn OAuth: redirect flows, not JSON, so handled here in do_GET.
        if u.path == "/api/oauth/linkedin/start":
            if not auth.linkedin_configured():
                return self._redirect("/#/signup?li=unconfigured")
            host = self._forwarded_host() or "127.0.0.1"
            url, state = auth.linkedin_start(host)
            return self._redirect(url, cookies=[auth.linkedin_state_cookie(state)])
        if u.path == "/api/oauth/linkedin/callback":
            code = (qs.get("code", [""])[0] or "").strip()
            state = (qs.get("state", [""])[0] or "").strip()
            expected = self._cookies().get(auth.LI_STATE_COOKIE)
            if not code or not state or state != expected:
                return self._redirect("/#/signup?li=state",
                                      cookies=[auth.linkedin_clear_state_cookie()])
            host = self._forwarded_host() or "127.0.0.1"
            conn = connect()
            try:
                try:
                    ui = auth.linkedin_exchange(code, host)
                    mid = linkedin_land(conn, ui)
                except Exception:
                    return self._redirect("/#/signup?li=failed",
                                          cookies=[auth.linkedin_clear_state_cookie()])
                # Land on Account so the member immediately sees the
                # fields we just pulled from LinkedIn (name, email,
                # avatar). Better feedback than a home-page welcome.
                return self._redirect(
                    "/#/account?li=synced",
                    cookies=[
                        auth.set_cookie_header(auth.make_session_cookie(mid)),
                        auth.linkedin_clear_state_cookie(),
                    ])
            finally:
                conn.close()
        if u.path.startswith("/shot/"):
            fn = os.path.basename(u.path[len("/shot/"):])
            fp = config.SCREENSHOT_DIR / fn
            if fp.exists() and fp.stat().st_size > 0:
                data = fp.read_bytes()
                self.send_response(200)
                ct = "image/jpeg" if data[:3] == bytes.fromhex("ffd8ff") else "image/png"
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404); self.end_headers(); return
        conn = connect()
        try:
            cookies = self._cookies()
            mid = _me(conn, cookies)
            authed = _me_from_session(conn, cookies) is not None
            if u.path == "/api/home":
                self._json(api_home(conn))
            elif u.path == "/api/geo":
                # Sensitive: per-market SEO data is members-only. The markets
                # INDEX (/api/markets) stays open so prospects see the breadth.
                if not authed:
                    self._json({"error": "auth_required"}, 401)
                else:
                    self._json(api_geo(conn, g("iso", "GB").upper(), g("vertical"), mid))
            elif u.path == "/api/site":
                # Sensitive: full site profile (traffic, trends, contacts) is members-only.
                if not authed:
                    self._json({"error": "auth_required"}, 401)
                else:
                    p = api_site(conn, g("domain"), mid)
                    self._json(p) if p else self._json({"error": "not_found"}, 404)
            elif u.path == "/api/markets":
                self._json(api_markets(conn))
            elif u.path == "/api/loyalty":
                self._json(reviews.loyalty_progress(conn, mid))
            elif u.path == "/api/plans":
                self._json({"plans": PLANS, "topup": config.SWAP_TOPUP_PRICE})
            elif u.path == "/api/pricing":
                self._json(api_pricing(conn))
            elif u.path == "/api/account":
                a = api_account(conn, mid)
                self._json(a) if a else self._json({"error": "not_found"}, 404)
            elif u.path == "/api/swaps":
                self._json(api_swaps(conn, mid))
            elif u.path == "/api/lists":
                self._json(api_lists(conn, mid))
            elif u.path == "/api/share/status":
                self._json(api_share_status(conn, mid))
            elif u.path == "/api/chat":
                try:
                    since_id = int(g("since", "0") or 0)
                except Exception:
                    since_id = 0
                try:
                    limit = int(g("limit", str(CHAT_DEFAULT_LIMIT)) or CHAT_DEFAULT_LIMIT)
                except Exception:
                    limit = CHAT_DEFAULT_LIMIT
                self._json(api_chat_list(conn, mid, g("room", "global"),
                                         since_id, limit))
            elif u.path == "/api/chat/rooms":
                self._json(api_chat_rooms(conn))
            elif u.path == "/api/me":
                a = swaps.access(conn, mid)
                row = conn.execute(
                    "SELECT handle, real_name, work_email, company, avatar_url "
                    "FROM chat_managers WHERE id=?",
                    (mid,)).fetchone()
                handle = row["handle"] if row else "You"
                # Pending matches — non-completed matches this member has NOT
                # yet agreed to. Powers the red dot on the Matches tab and
                # on the avatar chip in the top nav.
                pending_matches = 0
                try:
                    for m in swaps.list_matches(conn, mid):
                        if m.get("status") != "completed" and not m.get("you_agreed"):
                            pending_matches += 1
                except Exception:
                    pending_matches = 0
                self._json({
                    "handle": handle,
                    "authed": authed,
                    "real_name": row["real_name"] if row else None,
                    "company": row["company"] if row else None,
                    "work_email": row["work_email"] if row else None,
                    "avatar_url": row["avatar_url"] if row else None,
                    "plan": a.get("plan", "standard"),
                    "swaps": a.get("swaps"),
                    "unlimited": a.get("unlimited"),
                    "pending_matches": pending_matches,
                })
            else:
                self._json({"error": "not_found"}, 404)
        finally:
            conn.close()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except ValueError:
            payload = {}
        conn = connect()
        try:
            cookies = self._cookies()
            mid = _me(conn, cookies)
            authed = _me_from_session(conn, cookies) is not None
            # Auth-mutating endpoints handled first (they set their own cookies).
            if u.path == "/api/register":
                res, cookie_hdr = api_register(conn, payload)
                self._json(res, 200 if res.get("ok") else 400, set_cookie=cookie_hdr)
                return
            if u.path == "/api/login":
                res, cookie_hdr = api_login(conn, payload)
                self._json(res, 200 if res.get("ok") else 401, set_cookie=cookie_hdr)
                return
            if u.path == "/api/logout":
                self._json({"ok": True}, set_cookie=auth.clear_cookie_header())
                return
            if u.path == "/api/review":
                try:
                    res = reviews.submit(conn, mid, payload.get("domain"),
                                         payload.get("rating"), payload.get("body"))
                    res["loyalty"] = reviews.loyalty_progress(conn, mid)
                    self._json({"ok": True, **res})
                except reviews.ReviewError as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            elif u.path in ("/api/want", "/api/have"):
                try:
                    fn = swaps.register_want if u.path == "/api/want" else swaps.register_have
                    fn(conn, mid, payload.get("domain"))
                    self._json({"ok": True})
                except swaps.SwapError as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            elif u.path in ("/api/want/remove", "/api/have/remove"):
                dom = (payload.get("domain") or "").strip()
                if not dom:
                    self._json({"ok": False, "error": "domain_required"}, 400)
                    return
                table = "swap_wants" if u.path == "/api/want/remove" else "swap_haves"
                sid = conn.execute("SELECT id FROM sites WHERE domain=?", (dom,)).fetchone()
                if sid:
                    conn.execute(
                        f"DELETE FROM {table} WHERE manager_id=? AND site_id=?",
                        (mid, sid["id"]))
                    conn.commit()
                self._json({"ok": True})
            elif u.path == "/api/swap/agree":
                try:
                    res = swaps.agree(conn, int(payload.get("match_id") or 0), mid)
                    self._json({"ok": True, **(res or {})})
                except swaps.SwapError as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            elif u.path == "/api/signup":
                try:
                    res = api_signup(conn, payload)
                    self._json(res, 200 if res.get("ok") else 400)
                except Exception as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            elif u.path == "/api/account":
                # Refuse profile edits from an unauthenticated caller.
                # Otherwise the write lands on the shared demo user row
                # and the next visitor either overwrites it or sees the
                # last person's data. 401 so the client can react.
                if not authed:
                    self._json({"ok": False, "error": "not_authenticated"}, 401)
                else:
                    try:
                        self._json(api_account_update(conn, mid, payload))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 400)
            elif u.path == "/api/chat/send":
                if not authed:
                    self._json({"ok": False, "error": "not_authenticated"}, 401)
                else:
                    try:
                        res, code = api_chat_send(conn, mid, payload)
                        self._json(res, code)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 400)
            elif u.path == "/api/share/claim":
                try:
                    res = api_share_claim(conn, mid)
                    self._json(res, 200 if res.get("ok") else 400)
                except Exception as e:
                    self._json({"ok": False, "error": str(e)}, 400)
            else:
                self._json({"error": "not_found"}, 404)
        finally:
            conn.close()


def serve(port: int = 8766) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _H)
    print(f"Affswap member app on http://127.0.0.1:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------- #
# The single-page app (HTML + CSS + JS), served at /
# --------------------------------------------------------------------------- #
APP_HTML = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1'>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>
<title>Affswap</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#F1F5FB;--card:#fff;--line:#E4EBF4;--ink:#14213B;--ink2:#5B6B84;--ink3:#93A0B5;
 --blue:#4E97E6;--blue-d:#2E77CC;--blue-dd:#205CA6;--blue-050:#ECF3FD;--up:#12A150;--down:#E5484D;--gold:#F5A524;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;--mono:ui-monospace,Menlo,monospace}
body{margin:0;font-family:var(--sans);color:var(--ink);background:var(--bg)}
.shell{max-width:460px;margin:0 auto;min-height:100vh;position:relative;
 background:radial-gradient(700px 380px at 100% -6%,#DCEAFB,transparent 60%),linear-gradient(180deg,#F7FAFE,#eef4fb);
 box-shadow:0 0 0 1px var(--line)}
.scr{padding:16px 16px 92px;animation:fade .18s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.appbar{display:flex;align-items:center;gap:9px;padding:4px 2px 12px}
.appbar .bk{font-size:23px;color:var(--blue-d);cursor:pointer;line-height:1}
.brand{display:inline-flex;align-items:center;gap:8px}.brand svg{width:23px;height:23px}
.logo{font-weight:800;font-size:18px;letter-spacing:-.3px}.logo span{color:var(--blue-d)}
.av{margin-left:auto;width:32px;height:32px;border-radius:50%;background:var(--blue-050);color:var(--blue-dd);display:flex;align-items:center;justify-content:center;font:800 11px var(--sans)}
.title{font:800 16px var(--sans);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
h2{font:800 26px/1.1 var(--sans);letter-spacing:-.8px;margin:4px 2px 6px}h2 span{color:var(--blue-d)}
.hs{color:var(--ink2);font-size:13.5px;margin:0 2px 12px}
.lbl{color:var(--ink3);font:800 10.5px var(--sans);letter-spacing:.1em;text-transform:uppercase;margin:18px 4px 9px}
.lbl-sub{color:var(--ink3);font:700 11px var(--mono)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0 4px}
.stat-c{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:0 6px 18px -14px rgba(16,40,60,.28)}
.stat-ic{font-size:20px;line-height:1;margin-bottom:6px;min-height:22px}
.stat-n{font:800 27px var(--sans);letter-spacing:-.6px;color:var(--blue-dd);line-height:1}
.stat-l{color:var(--ink2);font:700 11.5px var(--sans);margin-top:5px}
.pulse{display:inline-block;width:13px;height:13px;border-radius:50%;background:var(--up);box-shadow:0 0 0 0 rgba(18,161,80,.5);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(18,161,80,.45)}70%{box-shadow:0 0 0 10px rgba(18,161,80,0)}100%{box-shadow:0 0 0 0 rgba(18,161,80,0)}}
.row{display:grid;grid-template-columns:1fr 46px auto;align-items:center;gap:8px;padding:13px 15px;text-decoration:none;color:var(--ink);border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s}
.row:last-child{border-bottom:none}.row:hover{background:#F7FAFE}
.fl{font-size:20px}.rk{width:20px;text-align:center;color:var(--blue-d);font:800 14px var(--sans)}
.r-mkt{display:flex;align-items:center;gap:10px;min-width:0}.r-mkt b{font-size:14.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r-sites{color:var(--ink2);font:700 13px var(--mono);text-align:right;font-variant-numeric:tabular-nums}
.r-tr{font-weight:800;font-size:14px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.u{color:var(--ink3);font-weight:600;font-size:10px}
.mkhead{display:grid;grid-template-columns:1fr 46px auto;gap:8px;padding:10px 15px;border-bottom:1px solid var(--line);color:var(--ink3);font:800 9.5px var(--sans);letter-spacing:.08em;text-transform:uppercase}
.mkhead span:nth-child(2),.mkhead span:last-child{text-align:right}
.rm{flex:1;min-width:0}.rm b{font-size:14.5px;font-weight:700;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rs{color:var(--ink3);font-size:11.5px}.rv{font-weight:800;font-size:14px;text-align:right}
.home-cols{display:block}.home-rail{margin-top:12px}
.railnote{color:var(--ink2);font-size:12.5px;line-height:1.6;background:var(--blue-050);border:1px solid #DCEAFB;border-radius:14px;padding:14px 15px;margin-top:11px}
.chev{color:var(--ink3);font-size:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;box-shadow:0 5px 16px -12px rgba(16,40,60,.25);overflow:hidden}
.promo{display:flex;align-items:center;gap:11px;background:linear-gradient(120deg,#4E97E6,#2E77CC);color:#fff;border-radius:16px;padding:14px 16px;box-shadow:0 14px 26px -12px rgba(46,119,204,.6);cursor:pointer;margin:2px 0}
.promo b{font-size:15px}.promo .s{font-size:12px;opacity:.9}.promo .g{margin-left:auto;font-size:20px;font-weight:800}
.tabs{display:flex;gap:6px;overflow-x:auto;margin:8px 0 12px;padding-bottom:2px;scrollbar-width:none}.tabs::-webkit-scrollbar{display:none}
.tab{white-space:nowrap;cursor:pointer;font:700 12.5px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:7px 14px;background:#fff}
.tab.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.sc{display:block;text-decoration:none;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:15px;padding:12px 13px;margin-bottom:10px;box-shadow:0 5px 16px -13px rgba(16,40,60,.25);cursor:pointer}
.sc .ct{display:flex;align-items:center;gap:8px}.sc .ct b{flex:1;font-size:15px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc .big{font:800 21px var(--sans)}.sc .per{color:var(--ink3);font:700 10px var(--mono);margin-left:4px}
.chips{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:7px}
.vchip{font:800 9px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--blue-dd);background:var(--blue-050);border-radius:999px;padding:3px 8px}
.tp{font:800 11px var(--mono);border-radius:999px;padding:2px 8px;margin-left:auto}.tp.up{color:var(--up);background:#E6F6EC}.tp.down{color:var(--down);background:#FCEBEC}.tp.flat{color:var(--ink3);background:#EEF2F0}
.also{color:var(--ink3);font-size:10.5px}
.pshot{width:100%;height:158px;object-fit:cover;object-position:top;border-radius:15px;border:1px solid var(--line);box-shadow:0 10px 24px -14px rgba(16,40,60,.3)}
.pshot.none{display:flex;align-items:center;justify-content:center;background:var(--blue-050);color:var(--ink3);font-weight:700}
.pname{font:800 21px var(--sans);letter-spacing:-.4px;margin:13px 2px 2px}.pdom{color:var(--ink3);font-size:12.5px;font-weight:600;margin:0 2px 8px}
.psiterow{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 2px 8px}.psiterow .pdom{margin:0}
.viewsite{text-decoration:none;font:800 11.5px var(--sans);color:var(--blue-dd);background:var(--blue-050);border:1px solid #CFE1F8;border-radius:10px;padding:7px 11px;white-space:nowrap}
.wh-help{color:var(--ink2);font-size:11.5px;line-height:1.5;margin:9px 2px 0;text-align:center}
.stat{display:flex;align-items:center;gap:12px;margin:10px 2px 4px}.stat b{font:800 30px var(--sans);letter-spacing:-1px}
.rev-sum{display:flex;align-items:center;gap:11px;padding:12px 13px;margin:12px 0}
.rate{font:800 26px var(--sans)}.stars{color:var(--gold);font-size:13px;letter-spacing:1px}.sub{color:var(--ink3);font-size:11.5px}
.addrev{margin-left:auto;cursor:pointer;border:1.5px solid var(--blue);color:var(--blue-dd);background:var(--blue-050);font:800 12px var(--sans);border-radius:11px;padding:9px 12px}
.addrev[disabled]{opacity:.5;border-color:var(--line);color:var(--ink3);background:#fff}
.lblrow{display:flex;align-items:center;justify-content:space-between;margin:16px 4px 8px}
.viewall{cursor:pointer;color:var(--blue-d);font:800 11.5px var(--sans)}
.mk{display:flex;align-items:center;gap:9px;padding:8px 8px}.mk .mn{flex:1;font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{flex:1;height:8px;background:#E9F0FA;border-radius:999px;overflow:hidden}.bar span{display:block;height:100%;background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.mv{width:54px;text-align:right;font:800 12px var(--sans)}
.rev{padding:9px 2px;border-top:1px solid var(--line)}.rev .rh{display:flex;justify-content:space-between;font:700 11.5px var(--sans);color:var(--ink2)}.rev .rst{color:var(--gold)}.rev .rb{font-size:12.5px;margin-top:2px}
.wh{display:flex;gap:10px;margin:14px 0 0}
.whb{flex:1;cursor:pointer;border-radius:13px;padding:13px;font:800 13px var(--sans);border:1.5px solid var(--line);background:#fff;color:var(--ink)}
.whb.on.want{border-color:var(--blue);color:var(--blue-dd);background:var(--blue-050)}.whb.on.have{border-color:#8FE3C4;color:var(--up);background:#EAF7F0}
.cta{width:100%;margin-top:12px;cursor:pointer;border:0;border-radius:14px;padding:14px;font:800 14.5px var(--sans);color:#fff;background:linear-gradient(120deg,#5AA0EA,#2E77CC);box-shadow:0 16px 30px -14px rgba(46,119,204,.7)}
.lock{margin-top:12px;text-align:center;color:var(--ink3);font-size:12px}
.empty{text-align:center;color:var(--ink3);padding:50px 12px;font-size:14px}
.chartcard{background:#fff;border:1px solid var(--line);border-radius:15px;padding:12px 13px 10px;margin:12px 0;box-shadow:0 5px 16px -13px rgba(16,40,60,.25)}
.cc-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px}
.rangeseg-btns .rgbtn{display:inline-block;cursor:pointer;font:800 10px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:4px 9px;margin-left:4px}
.rangeseg-btns .rgbtn.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.rangeseg{margin-top:10px}.chart{display:none}.chart.on{display:block}.spark{width:100%;height:92px;display:block}
.chart-note{color:var(--ink3);font:600 10px var(--mono);margin-top:7px;text-align:right}
.chart-empty{color:var(--ink3);font-size:12px;padding:22px 4px;text-align:center}
/* loyalty */
.loyal{background:#fff;border:1px solid var(--line);border-radius:18px;padding:17px;margin-bottom:13px;box-shadow:0 10px 30px -20px rgba(16,40,60,.28)}
.lt{display:flex;align-items:center;gap:12px;margin-bottom:12px}.lic{width:44px;height:44px;border-radius:13px;background:var(--blue-050);display:flex;align-items:center;justify-content:center;font-size:23px}
.lh{font:800 16px var(--sans)}.lsub{color:var(--ink3);font-size:12px}.earn{margin-left:auto;background:#E6F6EC;color:var(--up);font:800 10.5px var(--sans);border-radius:999px;padding:4px 9px}
.dots{display:flex;gap:6px;margin-bottom:11px}.d{flex:1;height:9px;border-radius:999px;background:#E4ECF6}.d.on{background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.lbig{font:800 15px var(--sans);line-height:1.35;margin-bottom:6px}.lbig b{color:var(--blue-d)}
.lrule{color:var(--ink2);font-size:12px;line-height:1.55}
/* plans */
.plan{position:relative;background:#fff;border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:11px;box-shadow:0 6px 18px -14px rgba(16,40,60,.25)}
.plan.pop{border-color:var(--blue);box-shadow:0 16px 30px -16px rgba(46,119,204,.45)}
.ribbon{position:absolute;top:-9px;right:14px;background:var(--blue);color:#fff;font:800 8.5px var(--mono);border-radius:5px;padding:3px 8px}
.pn{font-weight:800;font-size:15px}.pp{font:800 24px var(--sans);margin:2px 0}.psw{color:var(--ink2);font-size:12px;margin-bottom:10px}
.pbtn{width:100%;cursor:pointer;border-radius:12px;padding:11px;font:800 13px var(--sans)}
.pbtn.pri{border:0;color:#fff;background:linear-gradient(120deg,#5AA0EA,#2E77CC)}.pbtn.gho{background:#fff;border:1.5px solid var(--blue);color:var(--blue-dd)}
/* bottom tab bar */
.tabbar{position:fixed;left:50%;transform:translateX(-50%);bottom:0;width:100%;max-width:460px;display:flex;
 background:#fffdfffa;backdrop-filter:blur(10px);border-top:1px solid var(--line);padding:8px 8px calc(8px + env(safe-area-inset-bottom));z-index:30}
.tb{flex:1;text-align:center;cursor:pointer;color:var(--ink3);font:700 10px var(--sans);padding:5px 0}
.tb .i{font-size:19px;display:block;line-height:1.1;filter:grayscale(1) opacity(.6)}
.tb.on{color:var(--blue-d)}.tb.on .i{filter:none}
/* modal / sheet */
.ov{position:fixed;left:50%;transform:translateX(-50%);top:0;bottom:0;width:100%;max-width:460px;z-index:40;display:none;align-items:flex-end;background:rgba(16,32,60,.42)}
.ov.show{display:flex}
.sheet{background:#fff;width:100%;border-radius:22px 22px 0 0;padding:18px 16px calc(20px + env(safe-area-inset-bottom));animation:up .22s ease}
@keyframes up{from{transform:translateY(30px)}to{transform:none}}
.sh-h{display:flex;align-items:center;font:800 16px var(--sans)}.sh-h .x{margin-left:auto;cursor:pointer;color:var(--ink3)}
.sh-l{color:var(--ink3);font:800 10.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;margin:14px 2px 6px}
.starpick{font-size:32px;letter-spacing:6px;color:#DCE4EE;cursor:pointer}.starpick span.on{color:var(--gold)}
textarea{width:100%;border:1px solid var(--line);border-radius:12px;padding:11px 12px;font:400 13px var(--sans);min-height:74px;resize:none;color:var(--ink)}
.pop-c{margin:auto;background:#fff;border-radius:22px;padding:24px 20px;text-align:center;width:calc(100% - 32px);box-shadow:0 30px 60px -20px rgba(16,32,60,.5)}
.pop-ic{font-size:42px}.pop-t{font:800 20px var(--sans);margin-top:4px}.pop-s{color:var(--ink2);font-size:13.5px;margin:6px 0 14px}
.prog{height:9px;background:#E9F0FA;border-radius:999px;overflow:hidden}.prog span{display:block;height:100%;background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.px{color:var(--ink3);font:700 11.5px var(--sans);margin:7px 0 2px}
.toast{position:fixed;left:50%;bottom:86px;transform:translateX(-50%);background:var(--ink);color:#fff;font:700 13px var(--sans);padding:11px 16px;border-radius:12px;z-index:50;opacity:0;transition:opacity .2s;box-shadow:0 12px 24px -10px rgba(0,0,0,.4)}
.toast.show{opacity:1}
.geo-grid{display:block}
.prof{display:block}
.nav-brand,.nav-right{display:none}
/* ---- DESKTOP WEBSITE LAYOUT ---- */
@media(min-width:900px){
 body{background:linear-gradient(180deg,#F7FAFE,#eef4fb)}
 .shell{max-width:1160px;box-shadow:none;background:none;padding-top:66px}
 .scr{padding:28px 44px 64px}
 /* top navigation bar */
 .tabbar{position:fixed;top:0;bottom:auto;left:50%;transform:translateX(-50%);max-width:1160px;width:100%;
  align-items:center;justify-content:flex-start;gap:4px;padding:0 44px;height:66px;border-top:none;
  border-bottom:1px solid var(--line);background:#ffffffe8;backdrop-filter:blur(12px)}
 .nav-brand{display:inline-flex;align-items:center;gap:9px;cursor:pointer;margin-right:24px;text-decoration:none}
 .nav-brand svg{width:27px;height:27px}
 .nav-brand .logo{font-weight:800;font-size:20px;letter-spacing:-.4px;color:var(--ink)}.nav-brand .logo span{color:var(--blue-d)}
 .tb{flex:0 0 auto;padding:8px 14px;border-radius:9px;font:700 14px var(--sans);color:var(--ink2)}
 .tb .i{display:none}
 .tb.on{background:transparent;color:var(--blue-d)}
 .tb:not(.on):hover{color:var(--ink)}
 .nav-right{display:inline-flex;align-items:center;gap:13px;margin-left:auto}
 .nav-swaps{background:var(--blue-050);color:var(--blue-dd);font:800 12.5px var(--sans);border-radius:999px;padding:7px 14px}
 .nav-av{width:34px;height:34px;border-radius:50%;background:var(--blue-050);color:var(--blue-dd);display:flex;align-items:center;justify-content:center;font:800 12px var(--sans)}
 .abrand{display:none}
 .appbar{padding:2px 2px 16px}.appbar .title{font-size:18px}
 /* home */
 h2{font-size:44px;line-height:1.04;margin-top:4px}
 .hs{font-size:15px;margin-bottom:22px}
 .stats{grid-template-columns:repeat(4,1fr);gap:16px;margin:10px 0 28px}
 .stat-c{padding:18px}.stat-n{font-size:32px}.stat-l{font-size:12.5px}
 .lblrow{margin:4px 2px 12px}
 .home-cols{display:grid;grid-template-columns:1.9fr 1fr;gap:24px;align-items:start}
 .home-rail{margin-top:0}
 .row,.mkhead{grid-template-columns:1fr 90px 160px;padding-left:18px;padding-right:18px}
 .row{padding-top:15px;padding-bottom:15px}
 .promo{padding:18px 20px}
 .railnote{margin-top:14px;padding:18px 20px;font-size:13.5px}
 /* geo — market card grid */
 .geo-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
 .sc{margin-bottom:0}
 .tabs{margin:12px 0 18px}
 /* site profile — two columns */
 .prof{display:grid;grid-template-columns:1.05fr 1fr;gap:34px;align-items:start}
 .prof-l,.prof-r{min-width:0}
 .pshot{height:250px}.pname{font-size:26px}
 .loyal,.plan{max-width:none}
}
</style></head><body>
<div class='shell'><div id='app' class='scr'></div></div>
<nav class='tabbar' id='tabbar'></nav>
<div class='ov' id='ov'></div>
<div class='toast' id='toast'></div>
<svg width='0' height='0'><symbol id='mk' viewBox='0 0 44 44'><path d='M11 9 L18 22 L11 35' fill='none' stroke='#2E77CC' stroke-width='4.8' stroke-linecap='round' stroke-linejoin='round'/><path d='M33 9 L26 22 L33 35' fill='none' stroke='#7FB4F2' stroke-width='4.8' stroke-linecap='round' stroke-linejoin='round'/></symbol></svg>
<script>
var APP=document.getElementById('app'), OV=document.getElementById('ov'), TB=document.getElementById('tabbar');
var MK="<svg class='mkico'><use href='#mk'/></svg>";
function h(x){return (''+(x==null?'':x)).replace(/[&<>\"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
function fmt(n){n=+n||0;if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(1)+'k';return ''+Math.round(n);}
function fmtn(n){return (+n||0).toLocaleString('en-GB');}
function animateStats(){document.querySelectorAll('.stat-n').forEach(function(el){var to=+el.getAttribute('data-to')||0,st=null,dur=950;function step(ts){if(!st)st=ts;var p=Math.min(1,(ts-st)/dur);var e=1-Math.pow(1-p,3);el.textContent=fmtn(Math.round(to*e));if(p<1)requestAnimationFrame(step);}requestAnimationFrame(step);});}
function tcls(d){return d==='up'?'up':d==='down'?'down':'flat';}
function stars(r){r=Math.round(r||0);return '★★★★★☆☆☆☆☆'.slice(5-r,10-r);}
function gj(u){return fetch(u).then(function(r){return r.json();});}
function pj(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(function(r){return r.json();});}
function go(hash){location.hash=hash;}
function toggleMore(total){var m=document.getElementById('more');var b=document.getElementById('vabtn');if(!m)return;var hidden=m.style.display==='none';m.style.display=hidden?'block':'none';if(b)b.textContent=hidden?'Show less ▴':'See all '+total+' ▾';}
function toast(t){var e=document.getElementById('toast');e.textContent=t;e.classList.add('show');clearTimeout(e._t);e._t=setTimeout(function(){e.classList.remove('show');},1900);}
function closeOv(){OV.classList.remove('show');OV.innerHTML='';}
OV.addEventListener('click',function(e){if(e.target===OV)closeOv();});

var TABS=[['home','Home','🏠'],['markets','Markets','🌍'],['loyalty','Loyalty','🎁'],['plans','Plans','◆']];
function renderTabs(active){TB.innerHTML="<a class='nav-brand' onclick=\"go('#/home')\">"+MK+"<span class='logo'>Aff<span>swap</span></span></a>"+TABS.map(function(t){return "<div class='tb"+(t[0]===active?' on':'')+"' onclick=\"go('#/"+t[0]+"')\"><span class='i'>"+t[2]+"</span>"+t[1]+"</div>";}).join('')+"<span class='nav-right'><span class='nav-swaps'>⇄ 2 swaps left</span><span class='nav-av'>You</span></span>";}

function appbar(opts){opts=opts||{};
 if(opts.back!==undefined) return "<div class='appbar'><span class='bk' onclick=\"go('"+opts.back+"')\">‹</span><span class='title'>"+h(opts.title||'')+"</span></div>";
 return "<div class='appbar abrand'><span class='brand'>"+MK+"<span class='logo'>Aff<span>swap</span></span></span><span class='av'>You</span></div>";}

// ---------- HOME ----------
function renderHome(){renderTabs('home');
 gj('/api/home').then(function(d){
  var s=d.stats;
  var mk=d.markets.map(function(m){return "<div class='row' onclick=\"go('#/geo/"+m.iso+"')\"><span class='r-mkt'><span class='fl'>"+m.flag+"</span><b>"+h(m.name)+"</b></span><span class='r-sites'>"+m.count+"</span><span class='r-tr'>"+fmt(m.total_etv)+"<span class='u'>/mo</span></span></div>";}).join('');
  APP.innerHTML=appbar()
   +"<h2>Trade up<br>your <span>network.</span></h2><div class='hs'>The verified network for iGaming affiliate managers.</div>"
   +"<div class='stats'>"
     +"<div class='stat-c'><div class='stat-ic'>🧑‍💼</div><div class='stat-n' data-to='"+s.managers_joined+"'>0</div><div class='stat-l'>Managers joined</div></div>"
     +"<div class='stat-c'><div class='stat-ic'>🌐</div><div class='stat-n' data-to='"+s.websites+"'>0</div><div class='stat-l'>Affiliate websites</div></div>"
     +"<div class='stat-c'><div class='stat-ic'>🔄</div><div class='stat-n' data-to='"+s.swaps_to_date+"'>0</div><div class='stat-l'>Swaps to date</div></div>"
     +"<div class='stat-c'><div class='stat-ic'><span class='pulse'></span></div><div class='stat-n' data-to='"+s.online+"'>0</div><div class='stat-l'>Managers online</div></div>"
   +"</div>"
   +"<div class='lblrow'><span class='lbl' style='margin:0'>Markets</span><span class='lbl-sub'>"+d.total_markets+" countries · "+fmtn(s.websites)+" sites</span></div>"
   +"<div class='home-cols'>"
     +"<div class='card home-markets'><div class='mkhead'><span>Market</span><span>Sites</span><span>Monthly traffic</span></div>"+mk+"</div>"
     +"<div class='home-rail'>"
       +"<div class='promo' onclick=\"toast('Community Chat — coming soon')\"><span style='font-size:20px'>💬</span><div><b>Community Chat</b><div class='s'>"+s.online+" managers online now</div></div><span class='g'>→</span></div>"
       +"<div class='railnote'>Every website is <b>verified</b> and gated to <b>live monthly traffic</b>. Swap the affiliate contacts you have for the ones you want — no cash changes hands.</div>"
     +"</div>"
   +"</div>";
  animateStats();
 });}

// ---------- MARKETS / GEO ----------
var VERTS=[['','All'],['casino','Casino'],['sportsbook','Sportsbook'],['bingo','Bingo'],['poker','Poker']];
function renderMarkets(){go('#/geo/US');}
function renderGeo(iso,vert){renderTabs('markets');vert=vert||'';
 gj('/api/geo?iso='+iso+'&vertical='+vert).then(function(d){
  var tabs=VERTS.map(function(v){return "<span class='tab"+(v[0]===vert?' on':'')+"' onclick=\"go('#/geo/"+iso+(v[0]?'/'+v[0]:'')+"')\">"+v[1]+"</span>";}).join('');
  var cards=(d.cards||[]).map(function(c){
    var chips=(c.verticals||[]).map(function(v){return "<span class='vchip'>"+h(v)+"</span>";}).join('');
    return "<div class='sc' onclick=\"go('#/site/"+encodeURIComponent(c.domain)+"')\"><div class='ct'><b>"+h(c.name)+"</b><span class='tp "+tcls(c.trend_dir)+"'>"+h(c.trend)+"</span></div><div style='margin:6px 0 2px'><span class='big'>"+fmt(c.etv)+"</span><span class='per'>/mo here</span></div><div class='chips'>"+chips+"</div></div>";
  }).join('') || "<div class='empty'>No sites clear the traffic bar here yet.</div>";
  APP.innerHTML=appbar()
   +"<div style='display:flex;align-items:center;gap:10px;margin:2px 2px 6px'><span style='font-size:30px'>"+d.country.flag+"</span><div><div style='font:800 19px var(--sans)'>"+h(d.country.name)+"</div><div class='hs' style='margin:0'>"+d.count+" affiliates · live traffic</div></div></div>"
   +"<div class='tabs'>"+tabs+"</div><div class='geo-grid'>"+cards+"</div>";
 });}

// ---------- SITE PROFILE ----------
var _site=null;
// area+line sparkline (pale-blue), stretches to card width; vals oldest->newest
function sparkSVG(vals){
 if(!vals||vals.length<2) return '';
 var w=300,h=96,n=vals.length,mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals),rng=(mx-mn)||1;
 var pts=vals.map(function(v,i){return ((i/(n-1))*w).toFixed(1)+','+(h-8-((v-mn)/rng)*(h-20)).toFixed(1);});
 var line=pts.join(' '),last=pts[pts.length-1].split(',');
 return "<svg viewBox='0 0 "+w+" "+h+"' preserveAspectRatio='none' class='spark'>"
  +"<defs><linearGradient id='ag' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#4E97E6' stop-opacity='.20'/><stop offset='1' stop-color='#4E97E6' stop-opacity='0'/></linearGradient></defs>"
  +"<polygon points='0,"+h+" "+line+" "+w+","+h+"' fill='url(#ag)'/>"
  +"<polyline points='"+line+"' fill='none' stroke='#3F88E2' stroke-width='2.5'/>"
  +"<circle cx='"+last[0]+"' cy='"+last[1]+"' r='3.5' fill='#3F88E2'/></svg>";
}
// series: real MONTHLY snapshots once >=3 exist (DataForSEO historical), else
// 12 monthly points modeled from current etv + the real 90-day trend
function chartSeries(p){
 var hist=(p.traffic_history||[]).map(function(x){return +x.etv||0;}).filter(function(v){return v>0;});
 if(hist.length>=3) return {real:true,unit:'month',series:hist};
 var etv=+p.etv||0,n=12,dir=p.trend_dir,m=(p.trend||'').match(/([\d.]+)/),pct=m?+m[1]:8;
 var span=Math.min(0.5,Math.max(0.28,pct/100*2)),start=dir==='down'?(1+span):(1-span),s=[];
 for(var i=0;i<n;i++){var t=i/(n-1),f=start+(1-start)*t,wob=Math.sin((i+1)*12.9898)*43758.5453;wob=wob-Math.floor(wob);f=f*(1+(wob-0.5)*0.02);s.push(Math.max(1,etv*f));}
 s[n-1]=etv;return {real:false,unit:'month',series:s};
}
function chartCard(p){
 var cs=chartSeries(p),ser=cs.series;
 if(ser.length<2) return "<div class='chartcard'><div class='cc-top'><span class='lbl' style='margin:0'>Traffic history</span></div><div class='chart-empty'>Building traffic history — check back after the next weekly refresh.</div></div>";
 var wk=cs.unit==='week';
 function slc(w,mo){var k=wk?w:mo;return ser.slice(Math.max(0,ser.length-k));}
 var note=cs.real?"live monthly · DataForSEO":"modeled from current traffic &amp; 90-day trend — live history building";
 return "<div class='chartcard'><div class='cc-top'><span class='lbl' style='margin:0'>Traffic history</span>"
  +"<span class='rangeseg-btns'><span class='rgbtn on' onclick=\"chartTo(this,'3')\">3M</span><span class='rgbtn' onclick=\"chartTo(this,'6')\">6M</span><span class='rgbtn' onclick=\"chartTo(this,'12')\">1Y</span></span></div>"
  +"<div class='rangeseg'><div class='chart on' data-c='3'>"+sparkSVG(slc(13,3))+"</div><div class='chart' data-c='6'>"+sparkSVG(slc(26,6))+"</div><div class='chart' data-c='12'>"+sparkSVG(ser.slice())+"</div></div>"
  +"<div class='chart-note'>"+note+"</div></div>";
}
function chartTo(btn,r){var card=btn.closest('.chartcard');card.querySelectorAll('.rgbtn').forEach(function(x){x.classList.toggle('on',x===btn);});card.querySelectorAll('.chart').forEach(function(c){c.classList.toggle('on',c.dataset.c===r);});}
function renderSite(domain){renderTabs('markets');
 gj('/api/site?domain='+encodeURIComponent(domain)).then(function(p){
  if(p.error){APP.innerHTML=appbar({back:'#/home',title:'Not found'})+"<div class='empty'>Site not found.</div>";return;}
  _site=p;
  var hero=p.screenshot?"<img class='pshot' src='"+p.screenshot+"' onerror=\"this.onerror=null;this.replaceWith(Object.assign(document.createElement('div'),{className:'pshot none',textContent:'homepage'}))\">":"<div class='pshot none'>homepage</div>";
  var regs=p.regions||[];var mx=Math.max.apply(null,regs.map(function(r){return r.etv||0;}).concat([1]));
  function mrow(r){return "<div class='mk'><span class='fl'>"+r.flag+"</span><span class='mn'>"+h(r.name)+(r.primary?' ★':'')+"</span><span class='bar'><span style='width:"+Math.max(6,Math.round((r.etv||0)/mx*100))+"%'></span></span><span class='mv'>"+fmt(r.etv)+"</span></div>";}
  var top5=regs.slice(0,5).map(mrow).join('');
  var rest=regs.slice(5).map(mrow).join('');
  var va=rest?"<span class='viewall' id='vabtn' onclick='toggleMore("+regs.length+")'>See all "+regs.length+" ▾</span>":'';
  var chips=(p.verticals||[]).map(function(v){return "<span class='vchip'>"+h(v)+"</span>";}).join('');
  var rating=p.rating?("<span class='rate'>"+p.rating.toFixed(1)+"</span><span class='rr' style='display:flex;flex-direction:column'><span class='stars'>"+stars(p.rating)+"</span><span class='sub'>"+p.review_count+" reviews</span></span>"):"<span class='rr' style='display:flex;flex-direction:column'><span class='stars' style='color:#DCE4EE'>★★★★★</span><span class='sub'>No reviews yet</span></span>";
  var addBtn="<button class='addrev' onclick='openReview()' "+(p.you_reviewed?'disabled':'')+">"+(p.you_reviewed?'✓ Reviewed':'＋ Add review')+"</button>";
  var revList=(p.recent_reviews||[]).map(function(r){return "<div class='rev'><div class='rh'><span>"+h(r.handle||'—')+"</span><span class='rst'>"+stars(r.rating)+"</span></div>"+(r.body?"<div class='rb'>"+h(r.body)+"</div>":"")+"</div>";}).join('');
  APP.innerHTML=appbar({back:'#/geo/'+((_backGeo)||'US'),title:p.name})
   +"<div class='prof'><div class='prof-l'>"
   +hero
   +"<div class='pname'>"+h(p.name)+"</div>"
   +"<div class='psiterow'><span class='pdom'>"+h(p.domain)+"</span><a class='viewsite' href='https://"+h(p.domain)+"' target='_blank' rel='noopener'>View site ↗</a></div>"
   +"<div class='chips' style='margin:0 2px'>"+chips+"</div>"
   +"<div class='stat'><b>"+fmt(p.etv)+"</b><span class='u'>/mo total</span><span class='tp "+tcls(p.trend_dir)+"' style='margin-left:auto'>"+h(p.trend)+"</span></div>"
   +"<div class='card rev-sum'>"+rating+addBtn+"</div>"
   +(revList?"<div class='card' style='padding:4px 13px 8px'>"+revList+"</div>":"")
   +"</div><div class='prof-r'>"
   +chartCard(p)
   +"<div class='lblrow'><span class='lbl' style='margin:0'>Traffic by market</span>"+va+"</div>"
   +"<div class='card pad' style='padding:6px 6px'>"+top5+"<div class='more' id='more' style='display:none'>"+rest+"</div></div>"
   +"<div class='wh'><button class='whb want"+(p.you_want?' on':'')+"' id='wb' onclick=\"act('want')\">"+(p.you_want?'✓ Wanted':'＋ I want this')+"</button><button class='whb have"+(p.you_have?' on':'')+"' id='hb' onclick=\"act('have')\">"+(p.you_have?'✓ Have':'✓ I have this')+"</button></div>"
   +"<div class='wh-help'>Pick one — <b>Want</b> to be matched with a manager who has it, or <b>Have</b> to offer it to someone who wants it.</div>"
   +"<div class='lock'>🔒 Contact unlocks when you swap</div>"
   +"</div></div>";
 });}
var _backGeo='US';
function act(kind,req){if(!_site)return;
 pj('/api/'+kind,{domain:_site.domain}).then(function(r){
  if(r.ok){if(kind==='want'){_site.you_want=true;var b=document.getElementById('wb');if(b){b.classList.add('on');b.textContent='✓ Wanted';}toast('Requested — we will find you a match');}
   else{_site.you_have=true;var b=document.getElementById('hb');if(b){b.classList.add('on');b.textContent='✓ Have';}toast('Added to your Haves');}}
  else toast(r.error||'Something went wrong');});}

// review modal
function openReview(){if(!_site)return;var rt=0;
 OV.innerHTML="<div class='sheet'><div class='sh-h'>Review "+h(_site.name)+"<span class='x' onclick='closeOv()'>✕</span></div><div class='sh-l'>Your rating</div><div class='starpick' id='sp'><span data-v='1'>★</span><span data-v='2'>★</span><span data-v='3'>★</span><span data-v='4'>★</span><span data-v='5'>★</span></div><div class='sh-l'>Your experience</div><textarea id='rbody' placeholder='Traffic quality, payouts, communication…'></textarea><button class='cta' onclick='submitReview()'>Submit review</button><div class='lock' style='margin-top:10px'>Reviews are checked before they go live. Approved reviews earn loyalty.</div></div>";
 OV.classList.add('show');
 OV.querySelectorAll('#sp span').forEach(function(s){s.onclick=function(){window._rt=+s.dataset.v;OV.querySelectorAll('#sp span').forEach(function(o){o.classList.toggle('on',+o.dataset.v<=window._rt);});};});
 window._rt=0;}
function submitReview(){if(!window._rt){toast('Pick a rating');return;}
 pj('/api/review',{domain:_site.domain,rating:window._rt,body:(document.getElementById('rbody')||{}).value}).then(function(r){
  if(!r.ok){toast(r.error||'Could not submit');return;}
  var L=r.loyalty||{};var to=L.reviews_to_next_swap;
  OV.innerHTML="<div class='pop-c'><div class='pop-ic'>🎁</div><div class='pop-t'>Review submitted!</div><div class='pop-s'>"+(L.loyalty_active&&to?("<b>"+to+" more</b> approved reviews and you earn a <b>free swap</b>."):"Thanks — it's queued for approval.")+"</div>"+(L.loyalty_active?"<div class='prog'><span style='width:"+Math.round((L.approved_reviews%5)/5*100)+"%'></span></div><div class='px'>"+(L.approved_reviews%5)+" / 5 to your next free swap</div>":"")+"<button class='cta' onclick=\"closeOv();renderSite(_site.domain)\">Nice — keep going</button></div>";});}

// ---------- LOYALTY ----------
function renderLoyalty(){renderTabs('loyalty');
 gj('/api/loyalty').then(function(L){
  var active=L.loyalty_active;var ar=L.approved_reviews||0;
  var dots='';for(var i=0;i<5;i++)dots+="<span class='d"+(i<(ar%5)?' on':'')+"'></span>";
  var body;
  if(active){
   body="<div class='lbl' style='margin-top:2px'>Two ways to earn free swaps</div>"
    +"<div class='loyal'><div class='lt'><span class='lic'>⭐</span><div><div class='lh'>Write reviews</div><div class='lsub'>5 approved reviews → 1 free swap</div></div><span class='earn'>+"+L.free_swaps_from_reviews+" earned</span></div>"
    +"<div class='dots'>"+dots+"</div><div class='lbig'>"+(L.reviews_to_next_swap? (L.reviews_to_next_swap+" more approved reviews → <b>1 free swap</b>"):"Review a site to start")+"</div><div class='lrule'>Honest reviews of sites you know. Low-effort or fake ones are rejected and don't count.</div></div>"
    +"<div class='loyal'><div class='lt'><span class='lic'>➕</span><div><div class='lh'>Suggest a site</div><div class='lsub'>Approved affiliate → 1 free swap</div></div><span class='earn'>+"+(L.contributions.approved||0)+" earned</span></div><div class='lbig'>Submit an affiliate we don't list yet. When it's <b>approved</b>, you get <b>1 free swap</b>.</div><button class='cta' onclick=\"toast('Suggest a site — coming soon')\">＋ Suggest a site</button></div>";
  }else{
   body="<div class='loyal'><div class='lt'><span class='lic' style='font-size:26px;color:var(--blue-d)'>∞</span><div><div class='lh'>You're on Unlimited</div><div class='lsub'>Swaps are already unlimited</div></div></div><div class='lbig'>Loyalty rewards are <b>off</b> on Unlimited — you don't need free swaps.</div><div class='lrule'>Your reviews still help the community and build your reputation.</div></div>";}
  APP.innerHTML=appbar()+"<div class='hs' style='margin-top:2px'>Loyalty · <b>"+h(L.plan)+"</b> plan · "+(L.swaps_left==null?'∞':L.swaps_left)+" swaps left</div>"+body;
 });}

// ---------- PLANS ----------
function renderPlans(){renderTabs('plans');
 gj('/api/plans').then(function(d){
  var cards=d.plans.map(function(p){var pop=p.key==='pro';var sw=p.swaps==null?'Unlimited swaps':(p.swaps+' swaps / month');
   return "<div class='plan"+(pop?' pop':'')+"'>"+(pop?"<div class='ribbon'>POPULAR</div>":'')+"<div class='pn'>"+h(p.label)+"</div><div class='pp'>"+h(p.price)+"<span class='u'>/mo</span></div><div class='psw'>"+sw+"</div><button class='pbtn "+(pop?'pri':'gho')+"' onclick=\"toast('Checkout — coming soon')\">Choose "+h(p.label)+"</button></div>";}).join('');
  APP.innerHTML=appbar()+"<h2 style='font-size:22px'>Simple plans</h2><div class='hs'>Swap contacts, don't buy them. Top-up swaps at <b>"+h(d.topup)+"</b> each.</div>"+cards;
 });}

// ---------- ROUTER ----------
function route(){var hash=location.hash||'#/home';var parts=hash.slice(2).split('/');
 var s=parts[0]||'home';window.scrollTo(0,0);closeOv();
 if(s==='home')renderHome();
 else if(s==='markets')renderMarkets();
 else if(s==='geo'){_backGeo=parts[1]||'US';renderGeo(parts[1]||'US',parts[2]||'');}
 else if(s==='site')renderSite(decodeURIComponent(parts.slice(1).join('/')));
 else if(s==='loyalty')renderLoyalty();
 else if(s==='plans')renderPlans();
 else renderHome();}
window.addEventListener('hashchange',route);
route();
</script></body></html>"""


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8766)
