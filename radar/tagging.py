"""Vertical auto-tagging.

Every affiliate site is tagged with one or more verticals — casino / sportsbook /
bingo / poker. The tag is inferred from the domain (the only text signal we hold
for a bulk-imported site) and written as source='auto' rows in `site_verticals`.

Ownership rule (mirrors the API-vs-human boundary in radar/ownership.py):
  * 'auto'  rows are machine-owned and freely re-derivable — discovery, import
            and the weekly refresh may add/replace them.
  * 'human' rows are set by an operator in the back office and are AUTHORITATIVE.
            The auto-tagger NEVER touches a site that has any human row — it
            neither adds nor removes. "Machine proposes, human corrects."

Why domain-only: the imported catalogue has no stored SERP titles/snippets and
no descriptions — the domain string is the sole signal available at tag time.
That leaves a minority unclassifiable; those keep whatever they were imported
with (the 'casino' default) and a human can correct them in the back office.
"""
from __future__ import annotations

import re
import sqlite3

# The 4-value taxonomy (kept in sync with keywords.py / importer.py / alerts.py).
VERTICALS = ("casino", "sportsbook", "bingo", "poker")

# Domain-substring signals per vertical, ordered casino-last so a domain that
# names another vertical explicitly (oddschecker, pokerstars) isn't drowned by a
# stray casino token. A domain may match several — a site can be both.
_SIGNALS: dict[str, tuple[str, ...]] = {
    "sportsbook": (
        "sportsbook", "sports", "sport", "betting", "bookie", "bookmaker",
        "odds", "wager", "punter", "punting", "tipster", "tips", "handicap",
        "livescore", "football", "soccer", "matchbook", "betting",
        "scommesse", "apuestas", "apostas", "wetten", "parions",
    ),
    "poker": ("poker", "holdem", "rounder", "wsop", "wpt"),
    "bingo": ("bingo", "lotto", "lottery", "keno", "housie"),
    "casino": (
        "casino", "kasino", "slot", "spin", "reel", "jackpot",
        "pokie", "vegas", "roulette", "blackjack", "baccarat", "gamble",
        "gambling", "bonus", "freespin", "megaways",
    ),
}

# Known brands whose vertical the domain tokens can't reveal (e.g. "fotmob" has
# no sport token). Exact-stem match, checked before token matching. Mirrors the
# _KNOWN nicety in branding.py. A human can still override any of these.
_BRANDS: dict[str, tuple[str, ...]] = {
    # sports data / odds / tipping
    "fotmob": ("sportsbook",), "livescore": ("sportsbook",), "flashscore": ("sportsbook",),
    "sofascore": ("sportsbook",), "forebet": ("sportsbook",), "soccerway": ("sportsbook",),
    "whoscored": ("sportsbook",), "oddspedia": ("sportsbook",), "betexplorer": ("sportsbook",),
    "covers": ("sportsbook",), "actionnetwork": ("sportsbook",), "vegasinsider": ("sportsbook",),
    "legalsportsreport": ("sportsbook",), "sportinglife": ("sportsbook",), "olbg": ("sportsbook",),
    "bettingexpert": ("sportsbook",), "footystats": ("sportsbook",), "365scores": ("sportsbook",),
    "aiscore": ("sportsbook",), "xscores": ("sportsbook",), "scorebat": ("sportsbook",),
    "soccerstats": ("sportsbook",), "windrawwin": ("sportsbook",), "goal": ("sportsbook",),
    # poker
    "pokerstars": ("poker",), "pokernews": ("poker",), "pokerstrategy": ("poker",),
    "cardplayer": ("poker",), "twoplustwo": ("poker",), "upswingpoker": ("poker",),
    "ggpoker": ("poker",), "partypoker": ("poker",), "cardschat": ("poker",),
    # bingo
    "whichbingo": ("bingo",), "bingoport": ("bingo",), "bingosites": ("bingo",),
}

# trailing market suffixes to drop before matching (mirrors branding.py)
_CC = re.compile(r"-(uk|ie|de|se|br|us|ca|au|fr|it|es|pl|jp|nz|za)$", re.I)


def _stem(domain: str) -> str:
    """Normalise a domain to a lowercase alnum stem for substring matching."""
    s = re.sub(r"^https?://", "", domain or "", flags=re.I)
    s = re.sub(r"^www\.", "", s, flags=re.I).split("/")[0]
    s = re.sub(r"\.[a-z.]+$", "", s, flags=re.I)   # drop TLD
    s = _CC.sub("", s)                              # drop -uk / -de market suffix
    return re.sub(r"[^a-z0-9]", "", s.lower())


def classify(domain: str) -> list[str]:
    """Infer vertical(s) from a domain. Returns [] when nothing matches (the
    caller then leaves the existing tag in place rather than guessing)."""
    stem = _stem(domain)
    if not stem:
        return []
    if stem in _BRANDS:                     # curated known brands win
        return [v for v in VERTICALS if v in _BRANDS[stem]]
    hits = [v for v, toks in _SIGNALS.items() if any(t in stem for t in toks)]
    # stable, taxonomy order
    return [v for v in VERTICALS if v in hits]


def _has_human_vertical(conn: sqlite3.Connection, site_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM site_verticals WHERE site_id=? AND source='human' LIMIT 1",
        (site_id,)).fetchone() is not None


def add_auto_vertical(conn: sqlite3.Connection, site_id: int, vertical: str) -> None:
    """Add a single machine-owned vertical from a known-vertical source (the
    discovery keyword set, a CSV column). No-op if the site has a human override
    or the vertical is outside the taxonomy. Does not commit."""
    if vertical not in VERTICALS:
        return
    if _has_human_vertical(conn, site_id):
        return
    conn.execute(
        "INSERT OR IGNORE INTO site_verticals (site_id, vertical, source) VALUES (?,?, 'auto')",
        (site_id, vertical))


def autotag_site(conn: sqlite3.Connection, site_id: int,
                 domain: str | None = None) -> list[str]:
    """(Re)derive a site's verticals from its domain. Skips human-owned sites.
    Replaces the site's 'auto' rows with the inferred set ONLY when the domain
    yields a signal; when it doesn't, the existing tags are left untouched (so a
    bulk-import placeholder survives). Returns the verticals now set as 'auto'
    (empty when skipped/undetermined). Does not commit."""
    if _has_human_vertical(conn, site_id):
        return []
    if domain is None:
        row = conn.execute("SELECT domain FROM sites WHERE id=?", (site_id,)).fetchone()
        if not row:
            return []
        domain = row["domain"]
    computed = classify(domain)
    if not computed:
        return []
    conn.execute("DELETE FROM site_verticals WHERE site_id=? AND source='auto'", (site_id,))
    for v in computed:
        conn.execute(
            "INSERT OR IGNORE INTO site_verticals (site_id, vertical, source) VALUES (?,?, 'auto')",
            (site_id, v))
    return computed


def tag_all(conn: sqlite3.Connection) -> dict:
    """Auto-tag every site (idempotent). Human-owned sites are skipped. Does not
    commit — the caller owns the transaction."""
    rows = conn.execute("SELECT id, domain FROM sites").fetchall()
    retagged = 0
    for r in rows:
        if autotag_site(conn, r["id"], r["domain"]):
            retagged += 1
    return {"sites": len(rows), "retagged": retagged}
