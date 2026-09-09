"""The data-ownership boundary — made enforceable in code.

Brief: "Separate API-owned fields from human-owned fields and never let one
overwrite the other. A refresh must never wipe a human-entered contact."

We enforce this with an explicit ALLOW-LIST of columns the API is permitted to
write. `apply_api_update` is the ONLY way the refresh job mutates a site, and it
physically refuses to write anything outside that allow-list. Human-owned data
(classification, source, notes, and the entire site_contacts table) is not on
the list, so a refresh cannot touch it — even if a future bug tries.
"""
from __future__ import annotations

import sqlite3

from .db import log_change, now_iso

# Columns on `sites` the weekly API refresh is allowed to write. Everything
# else on the row — classification, source, human_notes — is human-owned.
API_WRITABLE_SITE_COLUMNS = frozenset({
    "etv", "top_country", "rank_best", "trend_pct", "trend_dir", "last_api_refresh",
})

# Human-owned surfaces the API must NEVER write. Listed explicitly so the
# guarantee is documented and testable.
HUMAN_OWNED = {
    "sites": frozenset({"classification", "source", "human_notes"}),
    "tables": frozenset({"site_contacts"}),
}


class OwnershipViolation(RuntimeError):
    pass


def apply_api_update(conn: sqlite3.Connection, site_id: int,
                     fields: dict, source: str) -> list[str]:
    """Write API-owned fields to a site. Returns the list of columns changed.

    Raises OwnershipViolation if asked to write a non-API-owned column — the
    guardrail that makes the ownership rule impossible to break silently.
    """
    illegal = set(fields) - API_WRITABLE_SITE_COLUMNS
    if illegal:
        raise OwnershipViolation(
            f"refresh tried to write human/protected columns: {sorted(illegal)}"
        )

    row = conn.execute(
        f"SELECT {', '.join(API_WRITABLE_SITE_COLUMNS)} FROM sites WHERE id = ?",
        (site_id,),
    ).fetchone()

    changed: list[str] = []
    for col, new_val in fields.items():
        old_val = row[col] if row else None
        if old_val != new_val:
            conn.execute(f"UPDATE sites SET {col} = ? WHERE id = ?", (new_val, site_id))
            log_change(conn, "sites", site_id, col, old_val, new_val,
                       owner="api", source=source)
            changed.append(col)
    if changed:
        conn.execute("UPDATE sites SET updated_at = ? WHERE id = ?",
                     (now_iso(), site_id))
    return changed


# --- Human-owned writers (admin actions). Separate entry points on purpose. --

def set_classification(conn: sqlite3.Connection, site_id: int,
                       classification: str, admin: str) -> None:
    old = conn.execute("SELECT classification FROM sites WHERE id = ?",
                       (site_id,)).fetchone()["classification"]
    conn.execute("UPDATE sites SET classification = ?, updated_at = ? WHERE id = ?",
                 (classification, now_iso(), site_id))
    log_change(conn, "sites", site_id, "classification", old, classification,
               owner="human", source=f"admin:{admin}")
    # Approving a site publishes it: stamp published_at the first time it
    # becomes an affiliate (this is what makes it visible in the public app).
    if classification == "affiliate":
        row = conn.execute("SELECT published_at FROM sites WHERE id = ?",
                           (site_id,)).fetchone()
        if not row["published_at"]:
            conn.execute("UPDATE sites SET published_at = ? WHERE id = ?",
                         (now_iso(), site_id))
            log_change(conn, "sites", site_id, "published_at", None, now_iso(),
                       owner="human", source=f"admin:{admin}")


def set_display_name(conn: sqlite3.Connection, site_id: int,
                     display_name: str | None, admin: str) -> None:
    """Human-owned brand name shown on list buttons. Blank clears it (falls back
    to a name derived from the domain). Never written by a refresh."""
    old = conn.execute("SELECT display_name FROM sites WHERE id = ?",
                       (site_id,)).fetchone()["display_name"]
    val = (display_name or "").strip() or None
    if val == old:
        return
    conn.execute("UPDATE sites SET display_name = ?, updated_at = ? WHERE id = ?",
                 (val, now_iso(), site_id))
    log_change(conn, "sites", site_id, "display_name", old, val,
               owner="human", source=f"admin:{admin}")


_VALID_VERTICALS = ("casino", "sportsbook", "bingo", "poker")


def set_verticals(conn: sqlite3.Connection, site_id: int, verticals, admin: str) -> None:
    """Human-owned vertical override. The operator's selection becomes the site's
    AUTHORITATIVE vertical set: existing tags are cleared and the chosen ones are
    re-inserted as source='human', which makes the auto-tagger skip this site
    forever (radar/tagging.py). Blank/invalid selections are ignored so a stray
    empty POST never wipes a site's verticals. Does not commit — the admin
    handler owns the commit (matches set_classification/upsert_contact)."""
    chosen = sorted({v for v in (verticals or []) if v in _VALID_VERTICALS})
    if not chosen:
        return
    old = [r["vertical"] for r in conn.execute(
        "SELECT vertical FROM site_verticals WHERE site_id=? ORDER BY vertical", (site_id,))]
    if old == chosen and all(  # already exactly this, and already human-owned → no-op
        r["source"] == "human" for r in conn.execute(
            "SELECT source FROM site_verticals WHERE site_id=?", (site_id,))):
        return
    conn.execute("DELETE FROM site_verticals WHERE site_id=?", (site_id,))
    for v in chosen:
        conn.execute(
            "INSERT INTO site_verticals (site_id, vertical, source) VALUES (?,?, 'human')",
            (site_id, v))
    conn.execute("UPDATE sites SET updated_at = ? WHERE id = ?", (now_iso(), site_id))
    log_change(conn, "site_verticals", site_id, "vertical",
               ",".join(old), ",".join(chosen), owner="human", source=f"admin:{admin}")


CONTACT_CHANNELS = ("contact_email", "contact_telegram", "contact_teams")


def upsert_contact(conn: sqlite3.Connection, site_id: int, *,
                   company_name=None, contact_name=None, contact_email=None,
                   contact_telegram=None, contact_teams=None,
                   uploaded_by="admin") -> None:
    """Human-owned. Matched by domain upstream; here by site_id. Reachable
    channels are email / Telegram / Teams — any subset may be present."""
    exists = conn.execute("SELECT 1 FROM site_contacts WHERE site_id = ?",
                          (site_id,)).fetchone()
    if exists:
        conn.execute(
            """UPDATE site_contacts SET company_name=?, contact_name=?,
               contact_email=?, contact_telegram=?, contact_teams=?,
               uploaded_by=?, updated_at=? WHERE site_id=?""",
            (company_name, contact_name, contact_email, contact_telegram,
             contact_teams, uploaded_by, now_iso(), site_id),
        )
    else:
        conn.execute(
            """INSERT INTO site_contacts (site_id, company_name, contact_name,
               contact_email, contact_telegram, contact_teams,
               uploaded_by, uploaded_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (site_id, company_name, contact_name, contact_email, contact_telegram,
             contact_teams, uploaded_by, now_iso(), now_iso()),
        )
    log_change(conn, "site_contacts", site_id, "contact", None,
               contact_email or contact_telegram or contact_teams or company_name,
               owner="human", source=f"admin:{uploaded_by}")


def add_to_blacklist(conn: sqlite3.Connection, site_id: int, reason: str,
                     country: str | None = None, admin: str = "admin") -> None:
    """Human-owned warning. Shown to users with the reason; excluded from the
    recommended lists. A refresh never touches this table. `country` (ISO) is an
    optional human-owned market tag so the warning shows under that country."""
    exists = conn.execute("SELECT reason FROM blacklist WHERE site_id=?",
                          (site_id,)).fetchone()
    if exists:
        conn.execute("UPDATE blacklist SET reason=?, country=COALESCE(?, country), "
                     "added_by=?, updated_at=? WHERE site_id=?",
                     (reason, country, admin, now_iso(), site_id))
        old = exists["reason"]
    else:
        conn.execute("INSERT INTO blacklist (site_id, reason, country, added_by, added_at) "
                     "VALUES (?,?,?,?,?)", (site_id, reason, country, admin, now_iso()))
        old = None
    log_change(conn, "blacklist", site_id, "reason", old, reason,
               owner="human", source=f"admin:{admin}")


def set_review_summary(conn: sqlite3.Connection, site_id: int, rating: float | None,
                       review_count: int, admin: str = "peer") -> None:
    """Peer-owned aggregate (Phase 2). Written by the reviews subsystem / seed,
    never by the API refresh. rating=None means 'no approved reviews yet'."""
    conn.execute(
        """INSERT INTO site_reviews (site_id, rating, review_count, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(site_id) DO UPDATE SET
             rating=excluded.rating, review_count=excluded.review_count,
             updated_at=excluded.updated_at""",
        (site_id, rating, review_count, now_iso()))


def recompute_review_summary(conn: sqlite3.Connection, site_id: int,
                             admin: str = "peer") -> None:
    """Rebuild the site_reviews cache from the APPROVED rows in `reviews`. Call
    after any review status change. rating=None when there are no approved
    reviews (the read path treats NULL as 'no reviews')."""
    row = conn.execute(
        "SELECT AVG(rating) AS avg_r, COUNT(*) AS n FROM reviews "
        "WHERE site_id=? AND status='approved'", (site_id,)).fetchone()
    avg_r, n = row["avg_r"], row["n"] or 0
    rating = round(avg_r, 1) if avg_r is not None else None
    set_review_summary(conn, site_id, rating, n, admin=admin)


def remove_from_blacklist(conn: sqlite3.Connection, site_id: int, admin: str = "admin") -> None:
    conn.execute("DELETE FROM blacklist WHERE site_id=?", (site_id,))
    log_change(conn, "blacklist", site_id, "reason", "(blacklisted)", None,
               owner="human", source=f"admin:{admin}")
