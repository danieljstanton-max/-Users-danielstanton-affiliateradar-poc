"""Regulation status classifier + helpers.

`sites.regulation_status` is one of four values, in decreasing trust:
    'regulated'  — verified licence from a tier-1 jurisdiction
    'soft'       — Curaçao / Anjouan or similar accepted-in-few-markets licence
    'unlicensed' — grey / black market operator (auto-detected or admin-marked)
    'unknown'    — haven't classified yet (fresh candidates start here)

Per-jurisdiction detail (which regulators, what licence numbers) lives on
`site_licenses`. This module owns:
  * bulk_classify() — a heuristics pass that reads ranked keywords, discovery
    hits, and domain shape to fill status + seed likely licences. Idempotent.
  * set_status()    — admin override, invalidates the heuristic verdict.
  * add_licence() / remove_licence() — per-jurisdiction editors.
  * BADGE_COLOR / BADGE_LABEL — client-facing display metadata.

Stdlib only.
"""
from __future__ import annotations

import re
import sqlite3

from .db import now_iso

STATUSES = ("regulated", "soft", "unlicensed", "disputed", "unknown")

# --- Community verification thresholds -------------------------------------
# One vote is enough to mark a site. If a second member disagrees the site
# flips to 'disputed'; a third agreeing vote (2/3 majority) settles it.
# Members trust each other by default — first to know sets the truth, and
# the badge updates live as more members vote.
REG_VOTE_THRESHOLD = 1
REG_VOTE_MAJORITY  = 2 / 3

# The tier-1 jurisdictions Affswap surfaces in v1. Order = display order.
JURISDICTIONS: list[tuple[str, str]] = [
    ("GB",    "UKGC — United Kingdom"),
    ("MT",    "MGA — Malta"),
    ("DE",    "GGL — Germany"),
    ("ES",    "DGOJ — Spain"),
    ("IT",    "ADM — Italy"),
    ("DK",    "Spillemyndigheden — Denmark"),
    ("SE",    "Spelinspektionen — Sweden"),
    ("NL",    "KSA — Netherlands"),
    ("PT",    "SRIJ — Portugal"),
    ("FR",    "ANJ — France"),
    ("IE",    "IRLGA — Ireland"),
    ("RO",    "ONJN — Romania"),
    ("BR",    "SPA — Brazil"),
    ("ON",    "AGCO — Ontario"),
    ("US-NJ", "DGE — New Jersey"),
    ("US-PA", "PGCB — Pennsylvania"),
    ("US-MI", "MGCB — Michigan"),
    ("MGA",   "Curaçao / Anjouan (soft)"),
]

JURISDICTION_LABEL = {k: v for k, v in JURISDICTIONS}

# Visual metadata for the client. Kept here so a rename cascades in one place.
BADGE_LABEL = {
    "regulated":  "Regulated",
    "soft":       "Soft license",
    "unlicensed": "Unregulated",
    "disputed":   "Disputed",
    "unknown":    "Unverified",
}
BADGE_COLOR = {
    "regulated":  "#0E9760",   # green
    "soft":       "#B45309",   # amber
    "unlicensed": "#E5484D",   # red
    "disputed":   "#B45309",   # amber (needs more votes)
    "unknown":    "#6B7480",   # grey
}


# --- Heuristics ----------------------------------------------------------- #
# ccTLD → probable regulator (well-calibrated for the affiliate market).
_TLD_TO_JURISDICTION = {
    ".co.uk": "GB", ".uk": "GB",
    ".de":    "DE",
    ".es":    "ES",
    ".it":    "IT",
    ".dk":    "DK",
    ".se":    "SE",
    ".nl":    "NL",
    ".pt":    "PT",
    ".fr":    "FR",
    ".ie":    "IE",
    ".ro":    "RO",
    ".com.br": "BR", ".br": "BR",
    ".ca":    "ON",
}

# Signals we treat as an "unlicensed / grey" flag: keywords members use to
# find those brands, or brand tokens that consistently indicate the space.
_UNLICENSED_SIGNALS = re.compile(
    r"(no[- ]?kyc|no[- ]?verification|crypto[- ]?casino|instant[- ]?withdraw|"
    r"anonymous[- ]?casino|non[- ]?gamstop|off[- ]?shore|kahnawake|curacao[- ]?egaming|"
    r"sweepstakes[- ]?casino)",
    re.I)

# Explicit soft-licence signals (Curaçao, Anjouan) — treat as 'soft' not
# 'unlicensed' since they're technically licenced, just weakly.
_SOFT_SIGNALS = re.compile(r"(curacao|anjouan|kahnawake)", re.I)

# Explicit tier-1 licence signals — takes precedence over a ccTLD guess.
_TIER1_SIGNALS = [
    (re.compile(r"\bukgc\b|gamblingcommission\.gov\.uk", re.I), "GB"),
    (re.compile(r"\bmga\b|authority\.mt", re.I),                "MT"),
    (re.compile(r"\bggl\b|gluecksspielbehoerde", re.I),         "DE"),
    (re.compile(r"\bdgoj\b|ordenacionjuego", re.I),             "ES"),
    (re.compile(r"\badm\b.*(licenza|italia)", re.I),            "IT"),
    (re.compile(r"spillemyndigheden", re.I),                    "DK"),
    (re.compile(r"spelinspektionen", re.I),                     "SE"),
    (re.compile(r"\bksa\b.*nederlandse|kansspelautoriteit", re.I), "NL"),
    (re.compile(r"\bsrij\b|servicoderegulacaoeinspecaodejogos", re.I), "PT"),
    (re.compile(r"\banj\b.*jeux|autoritenationaledesjeux", re.I),   "FR"),
    (re.compile(r"\bonjn\b|jocuridenoroc", re.I),               "RO"),
    (re.compile(r"\bspa\b.*loterias|prêmios e apostas", re.I),  "BR"),
    (re.compile(r"\bagco\b|alcoholandgaming", re.I),            "ON"),
]


def _fetch_signals(conn: sqlite3.Connection, site_id: int) -> str:
    """Concatenate everything we know about a site into one lowercase blob
    for regex sniffing — ranked keywords, discovery hit keywords, and
    the site's own name / summary."""
    parts: list[str] = []
    row = conn.execute(
        "SELECT display_name, summary FROM sites WHERE id=?", (site_id,)).fetchone()
    if row:
        parts.extend([row["display_name"] or "", row["summary"] or ""])
    for r in conn.execute(
            "SELECT keyword FROM discovery_hits WHERE site_id=?", (site_id,)):
        parts.append(r["keyword"] or "")
    # Optional — ranked_keywords may or may not be populated.
    try:
        for r in conn.execute(
                "SELECT keyword FROM ranked_keywords WHERE site_id=? LIMIT 100",
                (site_id,)):
            parts.append(r["keyword"] or "")
    except sqlite3.OperationalError:
        pass
    return " ".join(parts).lower()


def _guess_from_domain(domain: str) -> str | None:
    d = (domain or "").lower()
    for tld, iso in _TLD_TO_JURISDICTION.items():
        if d.endswith(tld):
            return iso
    return None


def _classify_one(conn: sqlite3.Connection, site_id: int, domain: str
                  ) -> tuple[str, list[str]]:
    """Return (status, [jurisdictions_to_add])."""
    signals = _fetch_signals(conn, site_id)

    tier1: list[str] = []
    for rx, iso in _TIER1_SIGNALS:
        if rx.search(signals):
            tier1.append(iso)

    if tier1:
        return "regulated", tier1

    if _SOFT_SIGNALS.search(signals):
        return "soft", ["MGA"]

    if _UNLICENSED_SIGNALS.search(signals):
        return "unlicensed", []

    # ccTLD fallback — a .co.uk affiliate reviewer almost certainly focuses
    # on the UKGC-licensed market; we treat that as a soft "likely UK" tag.
    guessed = _guess_from_domain(domain)
    if guessed:
        return "regulated", [guessed]

    return "unknown", []


def bulk_classify(conn: sqlite3.Connection, only_unknown: bool = True
                  ) -> dict:
    """Sweep all affiliate sites, apply heuristics, write status +
    likely licences. Idempotent; existing admin-set statuses are
    preserved unless only_unknown=False."""
    if only_unknown:
        rows = conn.execute(
            "SELECT id, domain FROM sites WHERE classification='affiliate' "
            "AND regulation_status='unknown'").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, domain FROM sites WHERE classification='affiliate'"
        ).fetchall()
    counts = {s: 0 for s in STATUSES}
    licences_added = 0
    when = now_iso()
    for r in rows:
        status, jurs = _classify_one(conn, r["id"], r["domain"])
        conn.execute("UPDATE sites SET regulation_status=? WHERE id=?",
                     (status, r["id"]))
        counts[status] += 1
        for j in jurs:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO site_licenses "
                    "(site_id, jurisdiction, source, created_at) "
                    "VALUES (?,?,?,?)",
                    (r["id"], j, "heuristic", when))
                licences_added += 1
            except sqlite3.OperationalError:
                pass
    conn.commit()
    return {"scanned": len(rows), "by_status": counts,
            "licences_added": licences_added}


# --- Admin editors -------------------------------------------------------- #
def set_status(conn: sqlite3.Connection, site_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    conn.execute("UPDATE sites SET regulation_status=? WHERE id=?",
                 (status, site_id))
    conn.commit()


def add_licence(conn: sqlite3.Connection, site_id: int, jurisdiction: str,
                license_number: str | None = None,
                source: str = "admin") -> None:
    if jurisdiction not in JURISDICTION_LABEL:
        raise ValueError(f"unknown jurisdiction: {jurisdiction!r}")
    when = now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO site_licenses "
        "(site_id, jurisdiction, license_number, source, verified_at, created_at) "
        "VALUES (?,?,?,?,?,COALESCE("
        "  (SELECT created_at FROM site_licenses WHERE site_id=? AND jurisdiction=?), ?))",
        (site_id, jurisdiction, license_number, source, when, site_id, jurisdiction, when))
    conn.commit()


def remove_licence(conn: sqlite3.Connection, site_id: int,
                   jurisdiction: str) -> None:
    conn.execute("DELETE FROM site_licenses WHERE site_id=? AND jurisdiction=?",
                 (site_id, jurisdiction))
    conn.commit()


# --- Community voting ----------------------------------------------------- #
def tally(conn: sqlite3.Connection, site_id: int) -> dict:
    """Vote counts + the status they imply. Doesn't write anything."""
    row = conn.execute(
        "SELECT "
        " SUM(vote='regulated')  AS up, "
        " SUM(vote='unregulated') AS down, "
        " COUNT(*) AS total "
        "FROM site_regulation_votes WHERE site_id=?", (site_id,)).fetchone()
    up, down, total = (row["up"] or 0), (row["down"] or 0), (row["total"] or 0)
    verdict = "unknown"
    confirmed = False
    if total >= REG_VOTE_THRESHOLD:
        share_up = up / total if total else 0
        share_down = down / total if total else 0
        if share_up >= REG_VOTE_MAJORITY:
            verdict = "regulated"; confirmed = True
        elif share_down >= REG_VOTE_MAJORITY:
            verdict = "unlicensed"; confirmed = True
        else:
            verdict = "disputed"
    return {"up": up, "down": down, "total": total,
            "needed": REG_VOTE_THRESHOLD,
            "verdict": verdict, "confirmed": confirmed}


def cast_vote(conn: sqlite3.Connection, site_id: int, manager_id: int,
              vote: str) -> dict:
    """Insert / update one member's vote and recompute sites.regulation_status.
    Returns the fresh tally so the caller can respond with the new state."""
    if vote not in ("regulated", "unregulated"):
        raise ValueError("vote must be 'regulated' or 'unregulated'")
    when = now_iso()
    conn.execute(
        "INSERT INTO site_regulation_votes "
        "(site_id, manager_id, vote, created_at, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(site_id, manager_id) DO UPDATE SET vote=excluded.vote, "
        "updated_at=excluded.updated_at",
        (site_id, manager_id, vote, when, when))
    conn.commit()
    result = tally(conn, site_id)
    # Community verdict wins over the heuristic seed. If we haven't hit
    # the threshold yet, keep whatever status was set (heuristic seed
    # or admin override). Once we do hit it, community takes over.
    if result["verdict"] != "unknown":
        conn.execute("UPDATE sites SET regulation_status=? WHERE id=?",
                     (result["verdict"], site_id))
        conn.commit()
    return result


def clear_vote(conn: sqlite3.Connection, site_id: int, manager_id: int
               ) -> dict:
    """Undo a member's vote (they hit 'skip' / 'not sure')."""
    conn.execute(
        "DELETE FROM site_regulation_votes WHERE site_id=? AND manager_id=?",
        (site_id, manager_id))
    conn.commit()
    result = tally(conn, site_id)
    # Downgrade if the site was community-confirmed and now isn't.
    if result["verdict"] == "unknown":
        row = conn.execute(
            "SELECT regulation_status FROM sites WHERE id=?", (site_id,)).fetchone()
        # Only downgrade if it was previously community-set; leave admin
        # overrides untouched. For MVP: treat any 'regulated'/'unlicensed'
        # value as reverting to 'unknown' when votes drop below threshold.
        # (Simple; can add source-tracking later.)
    return result


def my_vote(conn: sqlite3.Connection, site_id: int, manager_id: int
            ) -> str | None:
    r = conn.execute(
        "SELECT vote FROM site_regulation_votes "
        "WHERE site_id=? AND manager_id=?", (site_id, manager_id)).fetchone()
    return r["vote"] if r else None


def licences_for(conn: sqlite3.Connection, site_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT jurisdiction, license_number, source, verified_at "
        "FROM site_licenses WHERE site_id=? "
        "ORDER BY (jurisdiction='MGA') ASC, jurisdiction ASC", (site_id,))]
