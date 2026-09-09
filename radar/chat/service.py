"""Community-chat service — the gate, provisioning, and moderation.

The one rule (see docs/cometchat-integration.md): OUR DB is the only authority
on who may chat. CometChat never decides it. issue_session() re-runs the gate on
every call and mints a fresh token; losing verification is enforced by
revoke_access(), not by token expiry.
"""
from __future__ import annotations

import sqlite3

from ..db import now_iso
from .cometchat import CometChatClient

# The seven mocked rooms -> CometChat Groups (private).
ROOMS = [
    ("room_lounge", "Affiliate Lounge"),
    ("room_casino", "Casino"),
    ("room_sportsbook", "Sportsbook"),
    ("room_bingo", "Bingo"),
    ("room_poker", "Poker"),
    ("room_deals", "Deals & Partnerships"),
    ("room_new", "New to Affiliates"),
]


class NotVerified(Exception):
    """Raised when a non-verified manager tries to open a chat session."""


# --- provisioning -----------------------------------------------------------
def provision_rooms(conn: sqlite3.Connection, client: CometChatClient | None = None) -> dict:
    client = client or CometChatClient()
    for guid, name in ROOMS:
        client.create_group(guid, name, "private")
        conn.execute(
            "INSERT OR IGNORE INTO chat_rooms (guid, name, type, created_at) VALUES (?,?,?,?)",
            (guid, name, "private", now_iso()))
    conn.commit()
    return {"rooms": len(ROOMS), "calls": client.calls}


# --- the gate: issue a chat session ----------------------------------------
def issue_session(conn: sqlite3.Connection, manager_id: int,
                  client: CometChatClient | None = None) -> dict:
    """Verify -> ensure CometChat user (server-owned identity) -> ensure private
    group membership -> mint a rotated per-user auth token. Returns the payload
    the mobile app needs (and nothing secret)."""
    client = client or CometChatClient()
    m = conn.execute("SELECT * FROM chat_managers WHERE id=?", (manager_id,)).fetchone()
    if not m:
        raise NotVerified("unknown manager")
    # THE GATE — the only source of truth
    if not (m["linkedin_verified"] and m["work_email_confirmed"] and m["status"] == "verified"):
        raise NotVerified(m["status"])

    uid = m["cc_uid"]
    # server OWNS the display identity (anti-impersonation): reconcile every session
    client.ensure_user(uid, m["handle"], {"sector": m["sector"], "years": m["years"]})
    # private groups + server-managed membership (design A)
    for guid, _ in ROOMS:
        client.add_member(guid, uid)
    token = client.create_auth_token(uid, force=True)   # rotate prior tokens
    return {
        "appId": _safe(client.app_id), "region": client.region,
        "uid": uid, "authToken": token,
        "allowedGroups": [g for g, _ in ROOMS],
        "_mode": "LIVE" if client.live else "MOCK",
        "_calls": client.calls,
    }


def revoke_access(conn: sqlite3.Connection, manager_id: int,
                  client: CometChatClient | None = None) -> dict:
    """Verification lapsed / banned: actively revoke (tokens won't expire on
    their own). Delete tokens + drop from groups; deactivate if banned."""
    client = client or CometChatClient()
    m = conn.execute("SELECT * FROM chat_managers WHERE id=?", (manager_id,)).fetchone()
    if not m:
        return {"ok": False}
    uid = m["cc_uid"]
    client.delete_auth_tokens(uid)
    for guid, _ in ROOMS:
        client.kick_member(guid, uid)
    if m["status"] == "banned":
        client.deactivate_user(uid)
    return {"uid": uid, "calls": client.calls}


# --- moderation -------------------------------------------------------------
def ingest_report(conn: sqlite3.Connection, room_guid: str, message_id: str,
                  reported_uid: str, reporter_uid: str, reason: str,
                  client: CometChatClient | None = None) -> int:
    """A report from the app lands here. We fetch the REAL message from CometChat
    (never trust client-supplied text) and queue it for an operator."""
    client = client or CometChatClient()
    try:
        msg = client.get_message(message_id)
        snapshot = (msg.get("data") or {}).get("text", "")
    except Exception:
        snapshot = "(could not fetch message)"
    cur = conn.execute(
        """INSERT INTO chat_reports (room_guid, message_id, reported_uid, reporter_uid,
           reason, snapshot, status, created_at) VALUES (?,?,?,?,?,?,'open',?)""",
        (room_guid, message_id, reported_uid, reporter_uid, reason, snapshot, now_iso()))
    conn.commit()
    return cur.lastrowid


def list_reports(conn: sqlite3.Connection, status: str = "open") -> list:
    return conn.execute(
        "SELECT * FROM chat_reports WHERE status=? ORDER BY created_at DESC", (status,)
    ).fetchall()


def moderate(conn: sqlite3.Connection, report_id: int, action: str,
             by: str = "operator", client: CometChatClient | None = None) -> dict:
    """Operator decision on a report. action ∈ remove_message | kick | ban | dismiss."""
    client = client or CometChatClient()
    r = conn.execute("SELECT * FROM chat_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        return {"ok": False}
    if action == "remove_message" and r["message_id"]:
        client.delete_message(r["message_id"])
    elif action == "kick" and r["room_guid"] and r["reported_uid"]:
        client.kick_member(r["room_guid"], r["reported_uid"])
    elif action == "ban" and r["reported_uid"]:
        for guid, _ in ROOMS:
            client.ban_member(guid, r["reported_uid"])
        client.deactivate_user(r["reported_uid"])
        conn.execute("UPDATE chat_managers SET status='banned' WHERE cc_uid=?", (r["reported_uid"],))
    status = "dismissed" if action == "dismiss" else "actioned"
    conn.execute(
        "UPDATE chat_reports SET status=?, action=?, resolved_by=?, resolved_at=? WHERE id=?",
        (status, action, by, now_iso(), report_id))
    conn.commit()
    return {"report": report_id, "action": action, "status": status, "calls": client.calls}


def _safe(app_id: str) -> str:
    return app_id or "(set COMETCHAT_APP_ID)"


# --- demo seeding -----------------------------------------------------------
_DEMO_MANAGERS = [
    # cc_uid, handle, sector, years, linkedin, work_email, status, real_name
    ("mgr_8f21c", "Sarah K", "Casino", 7, 1, 1, "verified", "Sarah Kaminski"),
    ("mgr_4a7d0", "Mike R", "Casino", 4, 1, 1, "verified", "Michael Rourke"),
    ("mgr_1c93e", "Priya N", "Sportsbook", 6, 1, 1, "verified", "Priya Nair"),
    ("mgr_pend0", "Alex T", "Casino", 2, 1, 0, "pending", "Alex Turner"),   # email not confirmed → cannot chat
]


def seed_demo(conn: sqlite3.Connection, client: CometChatClient | None = None) -> dict:
    client = client or CometChatClient()
    provision_rooms(conn, client)
    for uid, handle, sector, years, li, em, status, real in _DEMO_MANAGERS:
        conn.execute(
            """INSERT OR IGNORE INTO chat_managers
               (cc_uid, handle, sector, years, linkedin_verified, work_email_confirmed,
                status, real_name, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, handle, sector, years, li, em, status, real, now_iso()))
    conn.commit()
    # a couple of reports waiting in the operator queue
    if not list_reports(conn):
        ingest_report(conn, "room_casino", "msg_10021", "mgr_4a7d0", "mgr_8f21c",
                      "spam / self-promotion", client)
        ingest_report(conn, "room_deals", "msg_10044", "mgr_1c93e", "mgr_8f21c",
                      "harassment", client)
    return {"managers": len(_DEMO_MANAGERS), "rooms": len(ROOMS), "reports": len(list_reports(conn))}


def manager_by_handle(conn: sqlite3.Connection, handle: str):
    return conn.execute("SELECT * FROM chat_managers WHERE handle=? OR cc_uid=?",
                        (handle, handle)).fetchone()
