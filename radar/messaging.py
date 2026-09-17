"""Operator → member in-app messages ("push messaging"), surfaced in My Account.

Stdlib only. A message with manager_id = NULL is a BROADCAST shown to every
member; a message with a manager_id is targeted to that one member. Read state is
tracked per member (member_message_reads) so it works for broadcasts too.
"""
from __future__ import annotations

import sqlite3

from .db import now_iso


def _ensure(conn: sqlite3.Connection) -> None:
    """Create the tables lazily (idempotent) — same pattern as chat_messages."""
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS member_messages ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  manager_id INTEGER REFERENCES chat_managers(id) ON DELETE CASCADE,"  # NULL = broadcast
        "  title TEXT NOT NULL,"
        "  body TEXT NOT NULL,"
        "  sent_by TEXT,"
        "  created_at TEXT NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_mm_mgr ON member_messages(manager_id, id DESC);"
        "CREATE TABLE IF NOT EXISTS member_message_reads ("
        "  message_id INTEGER NOT NULL REFERENCES member_messages(id) ON DELETE CASCADE,"
        "  manager_id INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,"
        "  read_at TEXT NOT NULL,"
        "  PRIMARY KEY (message_id, manager_id));")


def send(conn: sqlite3.Connection, manager_id, title: str, body: str,
         by: str = "backoffice") -> int:
    """Send a message. manager_id=None broadcasts to every member. Returns the id."""
    _ensure(conn)
    title = (title or "").strip()
    body = (body or "").strip()
    if not body and not title:
        raise ValueError("empty message")
    cur = conn.execute(
        "INSERT INTO member_messages (manager_id, title, body, sent_by, created_at) "
        "VALUES (?,?,?,?,?)",
        (manager_id, title or "Message from Affswap", body, by, now_iso()))
    conn.commit()
    return cur.lastrowid


def list_for(conn: sqlite3.Connection, mid: int, limit: int = 50) -> list:
    """This member's inbox — their targeted messages + every broadcast, newest first."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT m.id, m.title, m.body, m.created_at, m.manager_id, "
        "  (SELECT 1 FROM member_message_reads r WHERE r.message_id=m.id AND r.manager_id=?) AS is_read "
        "FROM member_messages m "
        "WHERE m.manager_id=? OR m.manager_id IS NULL "
        "ORDER BY m.id DESC LIMIT ?", (mid, mid, limit)).fetchall()
    return [{"id": r["id"], "title": r["title"], "body": r["body"], "at": r["created_at"],
             "broadcast": r["manager_id"] is None, "read": bool(r["is_read"])} for r in rows]


def unread_count(conn: sqlite3.Connection, mid: int) -> int:
    _ensure(conn)
    return conn.execute(
        "SELECT COUNT(*) FROM member_messages m "
        "WHERE (m.manager_id=? OR m.manager_id IS NULL) "
        "  AND NOT EXISTS (SELECT 1 FROM member_message_reads r "
        "                  WHERE r.message_id=m.id AND r.manager_id=?)",
        (mid, mid)).fetchone()[0]


def mark_read(conn: sqlite3.Connection, mid: int, message_id=None) -> int:
    """Mark one message read for this member, or all of them when message_id is None."""
    _ensure(conn)
    if message_id is None:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM member_messages WHERE manager_id=? OR manager_id IS NULL", (mid,))]
    else:
        ids = [int(message_id)]
    now = now_iso()
    for i in ids:
        conn.execute(
            "INSERT OR IGNORE INTO member_message_reads (message_id, manager_id, read_at) "
            "VALUES (?,?,?)", (i, mid, now))
    conn.commit()
    return len(ids)


def recipient_count(conn: sqlite3.Connection) -> int:
    """How many verified members a broadcast would reach (for the operator UI)."""
    return conn.execute(
        "SELECT COUNT(*) FROM chat_managers WHERE status='verified'").fetchone()[0]


# --- auto welcome, dropped into a member's inbox the moment they sign up -------
WELCOME_TITLE = "Welcome to Affswap 👋"
# {count} is replaced with the live registered-member count at send time, so the
# opening stays true forever instead of going stale.
WELCOME_BODY = """Thanks for joining Affswap.

You're now one of {count} affiliate managers who've registered and taken time to have a look around — which is exactly how a swap network gets stronger.

The platform works best when everyone gives a little to get a lot. Take some time to go through the site and mark the websites you Have and the websites you Want. The more people that do this, the more matches are created — and the stronger the whole ecosystem becomes.

Affswap is free to use, and you can keep earning free swaps through the Loyalty section in My Account. You can earn swaps by:

• Writing reviews
• Adding affiliate websites that aren't already listed
• Sharing Affswap on LinkedIn

If there's a website you work with, know the owner of, or have simply been following, just add the URL through the Loyalty section. Within minutes it's added to Affswap with its own traffic page, so you can watch whether its SEO traffic is growing or declining.

You'll also earn a free swap for every 5 reviews you leave — the more useful information everyone contributes, the more value there is for the whole network.

And please don't be afraid to send feedback. If there's a feature you'd like to see, let us know — we move quickly, and where it makes sense we'll build it for everyone.

Thanks again for being part of the early Affswap community.

— The Affswap Team"""


def send_welcome(conn: sqlite3.Connection, mid: int) -> int:
    """Send the welcome message to a brand-new member. Idempotent — never welcomes
    the same member twice — and best-effort (never blocks signup). Returns the new
    message id, or None if already welcomed / on any error."""
    try:
        _ensure(conn)
        already = conn.execute(
            "SELECT 1 FROM member_messages WHERE manager_id=? AND sent_by='system:welcome'",
            (mid,)).fetchone()
        if already:
            return None
        n = conn.execute("SELECT COUNT(*) FROM chat_managers WHERE status!='rejected'").fetchone()[0]
        body = WELCOME_BODY.replace("{count}", f"{n:,}")
        return send(conn, mid, WELCOME_TITLE, body, by="system:welcome")
    except Exception:
        return None
