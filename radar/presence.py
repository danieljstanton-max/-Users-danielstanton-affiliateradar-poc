"""Who's online, and who came in when.

Two signals, both written from the member app:

  * chat_managers.last_seen_at — bumped on authenticated requests, at most
    once a minute per member (in-process throttle, so chat polling every few
    seconds doesn't turn into a write storm). "Online" = seen in the last
    ONLINE_WINDOW_MINUTES.

  * member_activity — one row per session start:
        login     password sign-in
        linkedin  LinkedIn sign-in
        register  new account
        reset     signed in via a password-reset link
        visit     came back on an existing session cookie after being away
                  SESSION_GAP_MINUTES or more (sessions last 30 days, so most
                  returning members never "log in" — without this the weekly
                  log would miss them)

All timestamps are stored in now_iso() format (YYYY-MM-DDTHH:MM:SS+00:00) and
compared as strings against cutoffs built in the SAME format — never against
SQLite's datetime('now'), whose space separator sorts before 'T'.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .db import now_iso

ONLINE_WINDOW_MINUTES = 5
SESSION_GAP_MINUTES = 30
_TOUCH_EVERY_SECONDS = 60

_last_touch: dict[int, float] = {}
_lock = threading.Lock()

KIND_LABEL = {
    "login": "Password sign-in",
    "linkedin": "LinkedIn sign-in",
    "register": "New sign-up",
    "reset": "Password reset",
    "visit": "Returned",
}


def _iso_ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).replace(microsecond=0).isoformat()


def device_from_ua(ua: str | None) -> str:
    u = (ua or "").lower()
    return "mobile" if any(k in u for k in ("iphone", "android", "mobile", "ipad")) else "desktop"


def _record(conn: sqlite3.Connection, mid: int, kind: str, device: str) -> None:
    conn.execute(
        "INSERT INTO member_activity (manager_id, kind, device, created_at) VALUES (?,?,?,?)",
        (mid, kind, device, now_iso()))


def log_login(conn: sqlite3.Connection, mid: int, kind: str, ua: str | None = None) -> None:
    """An explicit sign-in. Also counts as being seen, so the member doesn't
    get a second 'Returned' row on their very next request."""
    _record(conn, mid, kind, device_from_ua(ua))
    conn.execute("UPDATE chat_managers SET last_seen_at=? WHERE id=?", (now_iso(), mid))
    conn.commit()
    with _lock:
        _last_touch[mid] = time.monotonic()


def touch(conn: sqlite3.Connection, mid: int, ua: str | None = None) -> None:
    """Call on every authenticated request. Cheap: no DB access unless this
    member hasn't been touched in the last minute."""
    now = time.monotonic()
    with _lock:
        # monotonic() can start near 0 at process start, so "absent" must mean
        # never-touched — not a default of 0.0, which reads as "just now".
        last = _last_touch.get(mid)
        if last is not None and now - last < _TOUCH_EVERY_SECONDS:
            return
        _last_touch[mid] = now
    row = conn.execute("SELECT last_seen_at FROM chat_managers WHERE id=?", (mid,)).fetchone()
    prev = row["last_seen_at"] if row else None
    if not prev or prev < _iso_ago(minutes=SESSION_GAP_MINUTES):
        _record(conn, mid, "visit", device_from_ua(ua))
    conn.execute("UPDATE chat_managers SET last_seen_at=? WHERE id=?", (now_iso(), mid))
    conn.commit()


def online_cutoff() -> str:
    return _iso_ago(minutes=ONLINE_WINDOW_MINUTES)


def online(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, handle, real_name, company, avatar_url, last_seen_at FROM chat_managers "
        "WHERE last_seen_at >= ? ORDER BY last_seen_at DESC", (online_cutoff(),)).fetchall()
    return [dict(r) for r in rows]


def activity_since(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    rows = conn.execute(
        "SELECT a.manager_id, a.kind, a.device, a.created_at, "
        "       m.handle, m.real_name, m.company, m.avatar_url "
        "FROM member_activity a JOIN chat_managers m ON m.id = a.manager_id "
        "WHERE a.created_at >= ? ORDER BY a.created_at DESC",
        (_iso_ago(days=days),)).fetchall()
    return [dict(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict:
    def n(sql, arg):
        return conn.execute(sql, (arg,)).fetchone()[0]
    return {
        "online": n("SELECT COUNT(*) FROM chat_managers WHERE last_seen_at >= ?", online_cutoff()),
        "today": n("SELECT COUNT(*) FROM chat_managers WHERE last_seen_at >= ?", _iso_ago(hours=24)),
        "week_members": n("SELECT COUNT(DISTINCT manager_id) FROM member_activity WHERE created_at >= ?",
                          _iso_ago(days=7)),
        "week_sessions": n("SELECT COUNT(*) FROM member_activity WHERE created_at >= ?", _iso_ago(days=7)),
    }


def ago(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return "—"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"
