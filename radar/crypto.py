"""Detect crypto-casino affiliates and queue them for review — nothing is tagged
crypto until a human confirms it (so every crypto page gets checked first).

Two detection signals feed the review queue:
  1. NAME  — the domain or brand name reads crypto (free, instant, prod-safe).
  2. SERP  — one of OUR approved affiliates ranks for crypto-casino keywords
             (a bounded DataForSEO pass; no-op in MOCK mode).

The operator then Confirms (applies the 'crypto' vertical via the normal tagging
path, so it also survives refreshes and can be edited by hand) or Dismisses.
'crypto' is a normal vertical, so a site can be both casino and crypto and the
app's existing vertical filter surfaces it with no extra plumbing.
"""
from __future__ import annotations

import re
import sqlite3

from .db import now_iso

_CRYPTO_RE = re.compile(
    r"crypto|bitcoin|\bbtc\b|\beth\b|blockchain|web3|no.?kyc|satoshi|"
    r"stake\b|roobet|bc\.?game|bitstarz|\bnano\b|coinplay|\bdoge\b",
    re.I)


def looks_crypto(text: str) -> bool:
    return bool(text and _CRYPTO_RE.search(text))


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS crypto_review ("
        "  site_id INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,"
        "  reason TEXT,"
        "  status TEXT NOT NULL DEFAULT 'pending',"   # pending | confirmed | dismissed
        "  detected_at TEXT NOT NULL,"
        "  reviewed_at TEXT);")


def _suggest(conn: sqlite3.Connection, site_id: int, reason: str) -> bool:
    """Queue a site for crypto review. Never disturbs one that's already been
    queued or reviewed, so re-running detection is safe. Returns True if newly added."""
    if conn.execute("SELECT 1 FROM crypto_review WHERE site_id=?", (site_id,)).fetchone():
        return False
    conn.execute(
        "INSERT INTO crypto_review (site_id, reason, status, detected_at) "
        "VALUES (?,?, 'pending', ?)", (site_id, reason, now_iso()))
    return True


def detect(conn: sqlite3.Connection, client=None,
           markets=("US", "GB", "CA", "AU"), progress=None) -> dict:
    """Find crypto candidates and QUEUE them (never tags). Name heuristic always
    runs; the SERP pass runs only when the provider is live."""
    _ensure(conn)
    added = 0
    # 1. name / domain heuristic — our approved affiliates that read crypto
    for r in conn.execute(
            "SELECT id, domain, display_name FROM sites WHERE classification='affiliate'"):
        if looks_crypto(r["domain"]) or looks_crypto(r["display_name"] or ""):
            if _suggest(conn, r["id"], "domain / brand name"):
                added += 1
    # 2. SERP pass — which of OUR affiliates rank for crypto-casino keywords
    serp_markets = 0
    if client is not None and getattr(client, "live", False):
        from .keywords import keywords_for
        from .locations import code_for
        for iso in markets:
            code = code_for(iso)
            if not code:
                continue
            for kw, lang in keywords_for(iso, "crypto"):
                try:
                    items = client.serp_organic(kw, code, lang)
                except Exception:
                    continue
                for it in items:
                    dom = (it.get("domain") or "").lower()
                    if not dom:
                        continue
                    site = conn.execute(
                        "SELECT id FROM sites WHERE domain=? AND classification='affiliate'",
                        (dom,)).fetchone()
                    if site and _suggest(conn, site["id"], f"ranks for “{kw}”"):
                        added += 1
            serp_markets += 1
            if progress:
                progress(serp_markets, len(markets), iso)
    conn.commit()
    return {"new_suggestions": added, "serp_markets": serp_markets,
            "pending": count(conn, "pending")}


def list_review(conn: sqlite3.Connection, status: str = "pending") -> list:
    _ensure(conn)
    return conn.execute(
        "SELECT cr.site_id, s.domain, s.display_name, s.etv, s.top_country, "
        "       cr.reason, cr.status "
        "FROM crypto_review cr JOIN sites s ON s.id = cr.site_id "
        "WHERE cr.status=? ORDER BY (s.etv IS NULL), s.etv DESC", (status,)).fetchall()


def count(conn: sqlite3.Connection, status: str = "pending") -> int:
    _ensure(conn)
    return conn.execute("SELECT COUNT(*) FROM crypto_review WHERE status=?",
                        (status,)).fetchone()[0]


def confirm(conn: sqlite3.Connection, site_id: int) -> None:
    """Human says yes → apply the crypto tag and mark it confirmed. Written as a
    'human' vertical: it's a reviewed decision, so it's sticky (a later auto-retag
    never removes it) and it sits alongside the site's other tags (casino etc.)."""
    _ensure(conn)
    conn.execute(
        "INSERT OR IGNORE INTO site_verticals (site_id, vertical, source) "
        "VALUES (?, 'crypto', 'human')", (site_id,))
    conn.execute("UPDATE crypto_review SET status='confirmed', reviewed_at=? WHERE site_id=?",
                 (now_iso(), site_id))
    conn.commit()


def dismiss(conn: sqlite3.Connection, site_id: int) -> None:
    """Not crypto → mark dismissed, never tag. (Detection won't re-queue it.)"""
    _ensure(conn)
    conn.execute("UPDATE crypto_review SET status='dismissed', reviewed_at=? WHERE site_id=?",
                 (now_iso(), site_id))
    conn.commit()


def crypto_affiliate_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM sites s WHERE s.classification='affiliate' "
        "AND EXISTS (SELECT 1 FROM site_verticals v WHERE v.site_id=s.id AND v.vertical='crypto')"
    ).fetchone()[0]
