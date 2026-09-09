"""Google Sheets mirror — a living, shareable database of the affiliate
catalogue plus a log of every contact that's been swapped.

Two tabs:
  * Affiliates — one row per site: domain, name, verticals, status, total etv,
    primary geo, and the full per-geo traffic split (every market it draws
    traffic from). Rebuilt on each sync.
  * Swaps — appended every time a contact changes hands: who sent which website
    + contact to whom, when.

Transport is deliberately dependency-free: instead of the Google client
libraries (which need pip), we POST JSON to a tiny **Apps Script Web App** bound
to the sheet (see docs/google-sheets-integration.md). Set SHEETS_WEBAPP_URL +
SHEETS_SECRET in .env to go LIVE. With them blank we run in MOCK mode and write
the exact rows to local CSVs under data/sheets/ — so you can see (and import)
precisely what would sync. Stdlib only.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import urllib.request

from . import config
from .branding import display_name

AFFILIATE_HEADER = ["domain", "name", "verticals", "status", "source", "total_etv",
                    "primary_geo", "geos_traffic", "markets", "has_contact",
                    "published_at", "last_refresh"]
SWAP_HEADER = ["timestamp", "from_manager", "to_manager", "website",
               "contact_shared", "flagged", "flag_reason", "match_id"]


# --- build the row sets -----------------------------------------------------
def affiliate_rows(conn: sqlite3.Connection, only_affiliates: bool = False) -> list:
    """One row per site — the affiliate database, with its full geo/traffic split."""
    q = ("SELECT id, domain, display_name, classification, source, etv, top_country, "
         "published_at, last_api_refresh FROM sites")
    if only_affiliates:
        q += " WHERE classification='affiliate'"
    q += " ORDER BY (etv IS NULL), etv DESC"
    out = []
    for s in conn.execute(q).fetchall():
        verts = [v["vertical"] for v in conn.execute(
            "SELECT vertical FROM site_verticals WHERE site_id=?", (s["id"],))]
        regs = conn.execute("SELECT country, etv FROM site_regions WHERE site_id=? "
                            "ORDER BY etv DESC", (s["id"],)).fetchall()
        geos = " | ".join(f"{r['country']}:{int(r['etv'] or 0)}" for r in regs)
        c = conn.execute("SELECT contact_email, contact_telegram, contact_teams "
                         "FROM site_contacts WHERE site_id=?", (s["id"],)).fetchone()
        has_contact = bool(c and (c["contact_email"] or c["contact_telegram"] or c["contact_teams"]))
        out.append([
            s["domain"], display_name(s["domain"], s["display_name"]), ", ".join(verts),
            s["classification"], s["source"],
            int(s["etv"]) if s["etv"] else "", s["top_country"] or "",
            geos, len(regs), "yes" if has_contact else "no",
            s["published_at"] or "", s["last_api_refresh"] or "",
        ])
    return out


def swap_rows(conn: sqlite3.Connection) -> list:
    """The full swaps log — every contact that has changed hands."""
    from . import swaps
    return [[r["at"], r["from"], r["to"], r["domain"], r["contact"],
             "yes" if r["flagged"] else "no", r["flag_reason"] or "", r["match_id"] or ""]
            for r in swaps.ledger(conn)]


def _swap_entry_row(e: dict) -> list:
    return [e.get("at", ""), e.get("from", ""), e.get("to", ""), e.get("domain", ""),
            e.get("contact", ""), "yes" if e.get("flagged") else "no",
            e.get("flag_reason") or "", e.get("match_id") or ""]


# --- transport: live Web App POST, or local CSV in mock ---------------------
def _sheet_path(tab: str):
    d = config.DATA_DIR / "sheets"
    d.mkdir(parents=True, exist_ok=True)
    return d / (tab.lower() + ".csv")


def _post(action: str, tab: str, header: list, rows: list) -> dict:
    """action ∈ {replace, append}. LIVE → POST to the Apps Script Web App;
    MOCK → write/append the local CSV mirror."""
    if config.sheets_is_live():
        payload = json.dumps({"secret": config.SHEETS_SECRET, "action": action,
                              "tab": tab, "header": header, "rows": rows}).encode()
        req = urllib.request.Request(config.SHEETS_WEBAPP_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:      # CONFIRM Web App deploy URL
            body = resp.read().decode()
        try:
            out = json.loads(body)
        except ValueError:
            out = {"raw": body[:200]}
        out.update({"mode": "LIVE", "tab": tab, "action": action})
        return out
    # MOCK — mirror to a local CSV so the "sheet" is real and inspectable
    path = _sheet_path(tab)
    new = action == "replace" or not path.exists() or path.stat().st_size == 0
    with open(path, "w" if action == "replace" else "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)
    return {"mode": "MOCK", "tab": tab, "action": action, "rows": len(rows), "file": str(path)}


# --- public API -------------------------------------------------------------
def sync_affiliates(conn: sqlite3.Connection, only_affiliates: bool = False) -> dict:
    """Rebuild the Affiliates tab from the current catalogue."""
    return _post("replace", "Affiliates", AFFILIATE_HEADER, affiliate_rows(conn, only_affiliates))


def sync_swaps(conn: sqlite3.Connection) -> dict:
    """Rebuild the Swaps tab from the full ledger."""
    return _post("replace", "Swaps", SWAP_HEADER, swap_rows(conn))


def sync_all(conn: sqlite3.Connection, only_affiliates: bool = False) -> dict:
    a = sync_affiliates(conn, only_affiliates)
    s = sync_swaps(conn)
    return {"affiliates": a, "swaps": s}


def append_swap(conn: sqlite3.Connection, entry: dict) -> dict:
    """Append a single completed swap to the Swaps tab — called the moment a
    contact is transferred, so the sheet updates in real time."""
    return _post("append", "Swaps", SWAP_HEADER, [_swap_entry_row(entry)])
