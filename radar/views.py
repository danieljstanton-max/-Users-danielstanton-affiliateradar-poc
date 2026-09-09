"""View assembly — the exact data shapes the front end would render.

These mirror two of the five mocked screens:
  * country_list  — stacked site cards for a market
  * site_profile  — hero metrics, 6-month(-ish) traffic history, contact block

Contact details are only included when include_contact=True (the verification
gate lives in the app/API layer; the view honours it here).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from .branding import display_name as _brand
from .locations import flag, name

# a site is flagged NEW in the public app for this many days after approval
NEW_WINDOW_DAYS = 14

# --- App-facing listing thresholds -----------------------------------------
# A site shows in a COUNTRY tab only if its organic traffic IN THAT COUNTRY
# clears a floor: COUNTRY_MIN_ETV normally, raised to LARGE_COUNTRY_MIN_ETV for
# large sites (>= LARGE_SITE_ETV total organic) so a giant global brand doesn't
# flood every country tab with tiny trickles of traffic. A site that qualifies
# in NO country is hidden from the app — but stays fully visible in the back
# office. refresh.py persists a market row for every country >= COUNTRY_MIN_ETV,
# so this gate has the per-country data it needs.
COUNTRY_MIN_ETV = 500
LARGE_SITE_ETV = 10_000
LARGE_COUNTRY_MIN_ETV = 1_500

# SQL predicate (needs aliases `s` = sites, `rg` = site_regions in scope):
# does the site clear the per-country bar in this market row?
_QUALIFIES = (f"rg.etv > (CASE WHEN s.etv >= {LARGE_SITE_ETV} "
              f"THEN {LARGE_COUNTRY_MIN_ETV} ELSE {COUNTRY_MIN_ETV} END)")


def _is_visible(conn: sqlite3.Connection, s) -> bool:
    """App visibility for a single site row: approved, not blacklisted, and it
    clears the per-country traffic bar in at least one market."""
    if s["classification"] != "affiliate":
        return False
    if conn.execute("SELECT 1 FROM blacklist WHERE site_id=?", (s["id"],)).fetchone():
        return False
    bar = LARGE_COUNTRY_MIN_ETV if (s["etv"] or 0) >= LARGE_SITE_ETV else COUNTRY_MIN_ETV
    return conn.execute(
        "SELECT 1 FROM site_regions WHERE site_id=? AND etv > ? LIMIT 1",
        (s["id"], bar)).fetchone() is not None


def _is_recent(published_at: str | None) -> bool:
    if not published_at:
        return False
    try:
        d = datetime.fromisoformat(published_at).date()
    except ValueError:
        return False
    return (datetime.now(timezone.utc).date() - d).days <= NEW_WINDOW_DAYS


def _other_markets(conn: sqlite3.Connection, site_id: int, exclude_iso: str) -> list:
    """The site's OTHER traffic markets (for an 'also in 🇩🇪 🇺🇸' hint)."""
    rows = conn.execute(
        "SELECT country FROM site_regions WHERE site_id=? AND country<>? ORDER BY etv DESC",
        (site_id, exclude_iso)).fetchall()
    return [{"iso": r["country"], "flag": flag(r["country"])} for r in rows]


def _region_breakdown(conn: sqlite3.Connection, site_id: int) -> list:
    """Every market the site pulls traffic from, biggest first (for the profile)."""
    rows = conn.execute(
        "SELECT country, etv, is_primary FROM site_regions WHERE site_id=? ORDER BY etv DESC",
        (site_id,)).fetchall()
    return [{"iso": r["country"], "flag": flag(r["country"]), "name": name(r["country"]),
             "etv": r["etv"], "primary": bool(r["is_primary"])} for r in rows]


def _trend_chip(pct, direction) -> str:
    if pct is None or direction is None:
        return "· new"
    arrow = "▲" if direction == "up" else "▼" if direction == "down" else "▬"
    sign = "+" if pct > 0 else ""
    return f"{arrow} {sign}{pct}%"


def country_list(conn: sqlite3.Connection, country: str,
                 vertical: str | None = None, sort: str = "traffic",
                 published_only: bool = True) -> dict:
    """A country's affiliates, filtered by vertical tab and sorted by one of
    three modes:
       traffic     — approved affiliates by estimated monthly traffic (desc)
       reviews     — approved affiliates by peer star rating (desc)
       blacklisted — the FLAGGED sites in this market, with the reason (a warning
                     view, deliberately separate from the recommended lists)
    """
    iso = country.upper()
    sort = sort if sort in ("traffic", "reviews", "blacklisted") else "traffic"
    header = {"country": {"iso": iso, "flag": flag(iso), "name": name(iso)},
              "vertical": vertical, "sort": sort}

    if sort == "blacklisted":
        rows = conn.execute(
            """SELECT s.id, s.domain, s.display_name, s.etv, s.top_country,
                      b.reason, b.added_at, c.company_name
               FROM blacklist b JOIN sites s ON s.id = b.site_id
               LEFT JOIN site_contacts c ON c.site_id = s.id
               WHERE b.country = ?
               ORDER BY b.added_at DESC, s.domain""", (iso,)).fetchall()
        cards = [{
            "domain": r["domain"], "name": _brand(r["domain"], r["display_name"]),
            "blacklisted": True, "reason": r["reason"],
            "company_name": r["company_name"], "etv": r["etv"],
            "traffic_origin": {"iso": r["top_country"],
                               "flag": flag(r["top_country"]) if r["top_country"] else "🏳️"},
        } for r in rows]
        return {**header, "count": len(cards), "cards": cards}

    # traffic / reviews modes — approved, non-blacklisted affiliates. A site is
    # in THIS market if it draws traffic here (site_regions), whatever vertical
    # it's tagged with. The etv shown is its traffic in THIS market.
    q = f"""
        SELECT DISTINCT s.id, s.domain, s.display_name, rg.etv AS region_etv, s.etv AS total_etv,
               s.top_country, s.trend_pct, s.trend_dir, s.classification, s.rank_best, s.published_at,
               rv.rating AS rating, rv.review_count AS review_count,
               (SELECT COUNT(*) FROM site_contacts c WHERE c.site_id = s.id
                  AND (c.contact_email IS NOT NULL OR c.contact_telegram IS NOT NULL
                       OR c.contact_teams IS NOT NULL)) AS has_contact
        FROM sites s
        JOIN site_regions rg ON rg.site_id = s.id AND rg.country = ?
        LEFT JOIN site_reviews rv ON rv.site_id = s.id
        WHERE 1=1
    """
    params: list = [iso]
    if vertical:
        q += " AND s.id IN (SELECT site_id FROM site_verticals WHERE vertical = ?)"
        params.append(vertical)
    if published_only:
        q += f" AND s.classification = 'affiliate' AND {_QUALIFIES} " + _NOT_BLACKLISTED
    if sort == "reviews":
        q += " ORDER BY (rv.rating IS NULL), rv.rating DESC, rv.review_count DESC"
    else:
        q += " ORDER BY (rg.etv IS NULL), rg.etv DESC"
    rows = conn.execute(q, params).fetchall()

    cards = []
    for r in rows:
        verts = [v["vertical"] for v in conn.execute(
            "SELECT vertical FROM site_verticals WHERE site_id = ?", (r["id"],))]
        cards.append({
            "domain": r["domain"],
            "name": _brand(r["domain"], r["display_name"]),
            "blacklisted": False,
            "etv": r["region_etv"],
            "total_etv": r["total_etv"],
            "also_in": _other_markets(conn, r["id"], iso),
            "traffic_origin": {"iso": r["top_country"],
                               "flag": flag(r["top_country"]) if r["top_country"] else "🏳️"},
            "trend": _trend_chip(r["trend_pct"], r["trend_dir"]),
            "trend_dir": r["trend_dir"],
            "rating": r["rating"],
            "review_count": r["review_count"] or 0,
            "verticals": verts,
            "classification": r["classification"],
            "is_new": _is_recent(r["published_at"]),
            "has_contact": bool(r["has_contact"]),
            "best_rank": r["rank_best"],
        })
    return {**header, "count": len(cards), "cards": cards}


# exclude blacklisted sites from every "recommended" surface
_NOT_BLACKLISTED = "AND s.id NOT IN (SELECT site_id FROM blacklist)"


def world_home(conn: sqlite3.Connection, limit: int = 10) -> dict:
    """Home screen — top affiliates WORLDWIDE, each flagged with the country
    where it has the most organic visitors (top_country)."""
    rows = conn.execute(
        f"""SELECT s.domain, s.display_name, s.etv, s.top_country, s.trend_pct, s.trend_dir
           FROM sites s
           WHERE s.classification='affiliate' {_NOT_BLACKLISTED}
             AND EXISTS (SELECT 1 FROM site_regions rg
                         WHERE rg.site_id = s.id AND {_QUALIFIES})
           ORDER BY s.etv DESC LIMIT ?""", (limit,)).fetchall()
    return {"count": len(rows), "affiliates": [
        {"rank": i + 1, "domain": r["domain"], "name": _brand(r["domain"], r["display_name"]), "etv": r["etv"],
         "origin": {"iso": r["top_country"], "flag": flag(r["top_country"]),
                    "name": name(r["top_country"]) if r["top_country"] else ""},
         "trend": _trend_chip(r["trend_pct"], r["trend_dir"]),
         "trend_dir": r["trend_dir"]}
        for i, r in enumerate(rows)]}


def countries_index(conn: sqlite3.Connection) -> dict:
    """Home flag grid — every market with published affiliates, plus its
    affiliate count, total monthly traffic, and how many sites are blacklisted
    there."""
    rows = conn.execute(
        f"""SELECT rg.country AS iso, COUNT(DISTINCT s.id) AS n,
                   COALESCE(SUM(rg.etv),0) AS total_etv
            FROM sites s JOIN site_regions rg ON rg.site_id = s.id
            WHERE s.classification='affiliate' AND {_QUALIFIES} {_NOT_BLACKLISTED}
            GROUP BY rg.country""").fetchall()
    bl = dict(conn.execute(
        "SELECT country, COUNT(*) FROM blacklist WHERE country IS NOT NULL GROUP BY country"
    ).fetchall())
    countries = [
        {"iso": r["iso"], "flag": flag(r["iso"]), "name": name(r["iso"]),
         "count": r["n"], "total_etv": round(r["total_etv"]),
         "blacklisted": bl.get(r["iso"], 0)}
        for r in rows]
    countries.sort(key=lambda c: (-c["total_etv"], c["name"]))
    return {"markets": len(countries), "countries": countries}


def blacklist(conn: sqlite3.Connection) -> dict:
    """Blacklist screen — flagged sites with the uploaded reason. A warning
    list, deliberately separate from the recommended affiliates."""
    rows = conn.execute(
        """SELECT s.domain, s.display_name, s.top_country, b.reason, b.added_at,
                  c.company_name
           FROM blacklist b JOIN sites s ON s.id = b.site_id
           LEFT JOIN site_contacts c ON c.site_id = s.id
           ORDER BY b.added_at DESC, s.domain""").fetchall()
    return {"count": len(rows), "entries": [
        {"domain": r["domain"], "name": _brand(r["domain"], r["display_name"]), "reason": r["reason"],
         "company_name": r["company_name"], "added_at": r["added_at"],
         "origin": {"iso": r["top_country"],
                    "flag": flag(r["top_country"]) if r["top_country"] else "🏳️"}}
        for r in rows]}


def market_movers(conn: sqlite3.Connection, country: str,
                  window_days: int = 90, n: int = 5, vertical: str | None = None) -> dict:
    """For a chosen market: the biggest risers and fallers over ~window_days,
    computed from our own weekly etv snapshots (published affiliates only).
    Optionally restricted to a vertical (Casino / Sportsbook / Bingo / Poker),
    matching the vertical tabs on the Market Movers screen."""
    q = (f"""SELECT DISTINCT s.id, s.domain, s.display_name, s.top_country
           FROM sites s JOIN site_regions rg ON rg.site_id = s.id
           WHERE s.classification='affiliate' AND rg.country = ? AND {_QUALIFIES} {_NOT_BLACKLISTED}""")
    params: list = [country.upper()]
    if vertical:
        q += " AND s.id IN (SELECT site_id FROM site_verticals WHERE vertical = ?)"
        params.append(vertical)
    sites = conn.execute(q, params).fetchall()

    movers = []
    for s in sites:
        snaps = conn.execute(
            "SELECT iso_week, etv FROM traffic_snapshots WHERE site_id=? ORDER BY iso_week",
            (s["id"],)).fetchall()
        if len(snaps) < 2:
            continue
        latest = snaps[-1]
        latest_d = date.fromisoformat(latest["iso_week"])
        # baseline = earliest snapshot still inside the window (≈ window_days ago)
        baseline = next((r for r in snaps[:-1]
                         if (latest_d - date.fromisoformat(r["iso_week"])).days <= window_days), None)
        if not baseline or not baseline["etv"]:
            continue
        actual_days = (latest_d - date.fromisoformat(baseline["iso_week"])).days
        pct = round((latest["etv"] - baseline["etv"]) / baseline["etv"] * 100, 1)
        movers.append({
            "domain": s["domain"], "name": _brand(s["domain"], s["display_name"]),
            "etv": latest["etv"],
            "etv_then": baseline["etv"], "pct": pct, "window_days": actual_days,
            "origin": {"iso": s["top_country"],
                       "flag": flag(s["top_country"]) if s["top_country"] else "🏳️"},
        })

    risers = sorted([m for m in movers if m["pct"] > 0], key=lambda m: -m["pct"])[:n]
    fallers = sorted([m for m in movers if m["pct"] < 0], key=lambda m: m["pct"])[:n]
    return {"country": {"iso": country.upper(), "flag": flag(country), "name": name(country)},
            "vertical": vertical, "window_days": window_days, "sample": len(movers),
            "risers": risers, "fallers": fallers}


def site_profile(conn: sqlite3.Connection, domain: str,
                 include_contact: bool = False,
                 published_only: bool = True) -> dict | None:
    s = conn.execute("SELECT * FROM sites WHERE domain = ?", (domain,)).fetchone()
    if not s:
        return None
    # App deep-links only resolve for a site that's actually live (approved +
    # clears the per-country traffic bar somewhere). Back office passes
    # published_only=False to preview a hidden site.
    if published_only and not _is_visible(conn, s):
        return None
    history = [
        {"week": r["iso_week"], "etv": r["etv"], "top_country": r["top_country"]}
        for r in conn.execute(
            "SELECT iso_week, etv, top_country FROM traffic_snapshots "
            "WHERE site_id = ? ORDER BY iso_week", (s["id"],))
    ]
    verts = [v["vertical"] for v in conn.execute(
        "SELECT vertical FROM site_verticals WHERE site_id = ?", (s["id"],))]
    shot = conn.execute("SELECT image_ref FROM screenshots WHERE site_id = ?",
                        (s["id"],)).fetchone()
    rev = conn.execute("SELECT rating, review_count FROM site_reviews WHERE site_id = ?",
                       (s["id"],)).fetchone()

    profile = {
        "domain": s["domain"],
        "name": _brand(s["domain"], s["display_name"]),
        "classification": s["classification"],
        "source": s["source"],
        "etv": s["etv"],
        "traffic_origin": {"iso": s["top_country"],
                           "flag": flag(s["top_country"]) if s["top_country"] else "🏳️"},
        "regions": _region_breakdown(conn, s["id"]),
        "trend": _trend_chip(s["trend_pct"], s["trend_dir"]),
        "trend_dir": s["trend_dir"],
        "verticals": verts,
        "screenshot": shot["image_ref"] if shot else None,
        "rating": rev["rating"] if rev else None,
        "review_count": (rev["review_count"] if rev else 0) or 0,
        "traffic_history": history,
        "contact": None,
        "contact_locked": True,
    }
    if include_contact:
        c = conn.execute("SELECT * FROM site_contacts WHERE site_id = ?",
                         (s["id"],)).fetchone()
        profile["contact_locked"] = False
        profile["contact"] = None if not c else {
            "company_name": c["company_name"], "contact_name": c["contact_name"],
            # each channel is either a reachable handle or None (not listed)
            "channels": {
                "email": c["contact_email"],
                "telegram": c["contact_telegram"],
                "teams": c["contact_teams"],
            },
        }
    return profile
