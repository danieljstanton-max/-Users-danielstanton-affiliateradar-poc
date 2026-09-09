"""Affswap sign-up + member approvals.

Sign-up is an application, not instant access. A prospective manager provides the
three legitimacy signals — **LinkedIn**, a confirmed **work email** on a real
company domain, and the **company + affiliate site** they manage — and lands in
the back-office *Member approvals* queue as `pending`. An admin reviews and
approves; approval calls `swaps.approve_manager`, which flips them to `verified`,
stamps `approved_at` (starting the 48-hour free trial) and opens their swap wallet.

Two steps are external integrations, kept as clearly-marked plug-in points (like
`alerts.deliver`): the **LinkedIn OAuth** handshake and the **work-email send**.
The app performs those; the resulting `linkedin_verified` / `work_email_confirmed`
flags flow into the same gate the rest of the app already trusts.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

from .db import now_iso

# free/consumer email providers are rejected — a work email is the anti-fake signal
_FREE_EMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.net", "live.com", "msn.com", "mail.com",
    "yandex.com", "zoho.com", "pm.me",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@([a-z0-9.-]+\.[a-z]{2,})$", re.I)


class SignupError(Exception):
    """An application could not be accepted (missing/invalid signal)."""


def _email_domain(email: str):
    m = _EMAIL_RE.match((email or "").strip().lower())
    return m.group(1) if m else None


def _uid(seed: str) -> str:
    return "mgr_" + hashlib.md5((seed + now_iso()).encode()).hexdigest()[:6]


# --- 1) apply ---------------------------------------------------------------
def apply(conn: sqlite3.Connection, *, real_name: str, handle: str, work_email: str,
          linkedin_url: str, company: str, site_url: str | None = None,
          sector: str | None = None, years: int | None = None,
          linkedin_verified: bool = True) -> dict:
    """Create a pending application. `linkedin_verified` reflects a completed
    LinkedIn OAuth (the app sets it true on success); the work email still needs
    confirming via the emailed link (`confirm_email`). `site_url` is optional —
    the company + work-email domain already identify the affiliate."""
    from .importer import normalize_domain
    real_name = (real_name or "").strip()
    handle = (handle or "").strip()
    company = (company or "").strip()
    if not (real_name and handle):
        raise SignupError("name and a display handle are required")
    domain = _email_domain(work_email)
    if not domain:
        raise SignupError("enter a valid work email")
    if domain in _FREE_EMAIL:
        raise SignupError("use your company work email, not a personal one")
    if not (linkedin_url or "").strip():
        raise SignupError("connect LinkedIn to apply")
    if not company:
        raise SignupError("tell us the company you work for")
    site = normalize_domain(site_url) if site_url else None

    cc_uid = _uid(work_email)
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO chat_managers
           (cc_uid, handle, sector, years, linkedin_verified, work_email_confirmed,
            status, real_name, work_email, linkedin_url, company, site_url,
            applied_at, created_at)
           VALUES (?,?,?,?,?,0,'pending',?,?,?,?,?,?,?)""",
        (cc_uid, handle, sector, years, 1 if linkedin_verified else 0,
         real_name, work_email.strip(), linkedin_url.strip(), company, site, ts, ts))
    conn.commit()
    return application(conn, cur.lastrowid)


def confirm_email(conn: sqlite3.Connection, member_id: int) -> dict:
    """Mark the work email confirmed — the app calls this from the email link.
    (Sending the email is the plug-in point; the token check lives in the app.)"""
    conn.execute("UPDATE chat_managers SET work_email_confirmed=1 WHERE id=?", (member_id,))
    conn.commit()
    return application(conn, member_id)


# --- 2) review queue --------------------------------------------------------
def application(conn, member_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM chat_managers WHERE id=?", (member_id,)).fetchone()
    return _app_dict(r) if r else None


def _app_dict(r) -> dict:
    ready = bool(r["linkedin_verified"] and r["work_email_confirmed"])
    return {
        "id": r["id"], "handle": r["handle"], "real_name": r["real_name"],
        "work_email": r["work_email"], "linkedin_url": r["linkedin_url"],
        "company": r["company"], "site_url": r["site_url"],
        "sector": r["sector"], "years": r["years"], "status": r["status"],
        "linkedin_verified": bool(r["linkedin_verified"]),
        "work_email_confirmed": bool(r["work_email_confirmed"]),
        "ready_for_review": ready, "applied_at": r["applied_at"],
    }


def list_applications(conn: sqlite3.Connection, status: str = "pending") -> list:
    rows = conn.execute(
        "SELECT * FROM chat_managers WHERE status=? ORDER BY applied_at DESC, id DESC",
        (status,)).fetchall()
    return [_app_dict(r) for r in rows]


# --- 3) decision ------------------------------------------------------------
def approve(conn: sqlite3.Connection, member_id: int, by: str = "operator") -> dict:
    """Approve an application → verified, 48h trial starts, swap wallet opened.
    Both automated signals must be in first."""
    from . import swaps
    m = conn.execute("SELECT * FROM chat_managers WHERE id=?", (member_id,)).fetchone()
    if not m:
        return {"ok": False, "error": "unknown"}
    if m["status"] != "pending":
        return {"ok": False, "error": f"already {m['status']}"}
    if not (m["linkedin_verified"] and m["work_email_confirmed"]):
        return {"ok": False, "error": "not yet verified — needs LinkedIn + confirmed work email"}
    acc = swaps.approve_manager(conn, member_id)      # verified + approved_at + wallet + trial
    return {"ok": True, "handle": m["handle"], "trial_ends_at": acc.get("trial_ends_at"),
            "state": acc.get("state")}


def reject(conn: sqlite3.Connection, member_id: int, reason: str | None = None,
           by: str = "operator") -> dict:
    r = conn.execute("SELECT status FROM chat_managers WHERE id=?", (member_id,)).fetchone()
    if not r or r["status"] != "pending":
        return {"ok": False}
    conn.execute("UPDATE chat_managers SET status='rejected' WHERE id=?", (member_id,))
    conn.commit()
    return {"ok": True, "id": member_id, "reason": reason}


# --- demo -------------------------------------------------------------------
_DEMO_APPS = [
    # real_name, handle, work_email, linkedin, company, sector, years, li_ok, confirm
    ("Nadia Rossi", "Nadia R", "nadia@spinpalace-media.example",
     "linkedin.com/in/nadiarossi", "SpinPalace Media", "Casino", 5, True, True),
    ("Tom Becker", "Tom B2", "tom@betedge.example",
     "linkedin.com/in/tombecker", "BetEdge Ltd", "Sportsbook", 3, True, False),
    ("Jed Fry", "Jed F", "jed@gmail.com",   # will be rejected at apply() — free email
     "linkedin.com/in/jedfry", "Solo Affiliate", "Casino", 1, True, True),
]


def seed_demo(conn: sqlite3.Connection) -> dict:
    made, refused = [], 0
    for real, handle, email, li, comp, sector, years, li_ok, confirm in _DEMO_APPS:
        try:
            a = apply(conn, real_name=real, handle=handle, work_email=email, linkedin_url=li,
                      company=comp, sector=sector, years=years, linkedin_verified=li_ok)
            if confirm:
                confirm_email(conn, a["id"])
            made.append(a["id"])
        except SignupError:
            refused += 1
    return {"applied": len(made), "refused_at_signup": refused,
            "pending": len(list_applications(conn))}
