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
