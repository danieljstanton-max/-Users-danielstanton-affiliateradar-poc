"""Weekly refresh + trend.

For every site with a domain: pull Domain Rank Overview across our candidate
markets, derive total etv + the top traffic-origin country, append a weekly
snapshot, and compute the trend from OUR OWN stored history (no repeated paid
historical calls). All writes go through ownership.apply_api_update, so the
refresh can only touch API-owned columns — human classification and contacts
are structurally out of reach.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import ownership, tagging
from .locations import all_isos, code_for, iso_for, language_for
from .providers.dataforseo import DataForSEOClient
from .views import COUNTRY_MIN_ETV  # per-country listing floor — see radar/views.py

# Weeks of history the demo builds. 13 weekly snapshots ≈ 90 days, enough for
# both the short-term "trend vs last month" and the 90-day Market Movers view.
HISTORY_WEEKS = 13
LATEST_WEEK = HISTORY_WEEKS - 1
TREND_BASELINE_DAYS = 21  # short-term trend: current vs ~3 weeks earlier
# A site "files under" every market it draws at least COUNTRY_MIN_ETV monthly
# organic visits from (plus its top market, always, for the profile). Storing on
# an absolute floor — not a % share — is what lets the app apply the per-country
# listing thresholds (500 / 1500 for large sites); see radar/views.py.


def _write_regions(conn: sqlite3.Connection, site_id: int, by_country: dict,
                   total_etv: float, top_country: str) -> None:
    """Persist EVERY market the site pulls meaningful traffic from — the multi-
    region tags. Rewritten each refresh (API-owned)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("DELETE FROM site_regions WHERE site_id=?", (site_id,))
    for iso, etv in by_country.items():
        # persist every market at/above the app's per-country listing floor,
        # plus the top market always (so the profile shows a primary even for a
        # tiny site). The app then gates on 500 / 1500 at query time.
        if iso == top_country or etv >= COUNTRY_MIN_ETV:
            conn.execute(
                "INSERT INTO site_regions (site_id, country, etv, is_primary, updated_at) "
                "VALUES (?,?,?,?,?)", (site_id, iso, etv, 1 if iso == top_country else 0, now))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def iso_week_for(week_index: int) -> str:
    """Map a week index (0..LATEST_WEEK) to a real, ascending Monday date, with
    the latest index landing on the current week."""
    anchor = _monday(datetime.now(timezone.utc).date())
    d = anchor - timedelta(weeks=(LATEST_WEEK - week_index))
    return d.isoformat()


def _compute_trend(conn: sqlite3.Connection, site_id: int,
                   current_week: str, current_etv: float) -> tuple[float, str] | None:
    """Trend from own snapshot history. Returns (pct, direction) or None."""
    rows = conn.execute(
        "SELECT iso_week, etv FROM traffic_snapshots WHERE site_id = ? ORDER BY iso_week",
        (site_id,),
    ).fetchall()
    if len(rows) < 2:
        return None
    cur_d = date.fromisoformat(current_week)
    # pick the snapshot closest to TREND_BASELINE_DAYS before current; else oldest
    baseline = None
    best_gap = None
    for r in rows:
        if r["iso_week"] >= current_week:
            continue
        gap = (cur_d - date.fromisoformat(r["iso_week"])).days
        score = abs(gap - TREND_BASELINE_DAYS)
        if best_gap is None or score < best_gap:
            best_gap, baseline = score, r
    if baseline is None or not baseline["etv"]:
        return None
    pct = round((current_etv - baseline["etv"]) / baseline["etv"] * 100, 1)
    direction = "up" if pct > 1 else "down" if pct < -1 else "flat"
    return pct, direction


def refresh_all(conn: sqlite3.Connection, week_index: int = LATEST_WEEK,
                client: DataForSEOClient | None = None) -> dict:
    client = client or DataForSEOClient(week=week_index)
    iso_week = iso_week_for(week_index)
    # Probe every curated market so we see a site's FULL per-country traffic split
    # (not just where it was discovered) — that's what lets one site file under
    # multiple regions.
    market_codes = sorted({c for c in (code_for(iso) for iso in all_isos()) if c})

    sites = conn.execute("SELECT id, domain FROM sites ORDER BY id").fetchall()
    if not sites:
        return {"iso_week": iso_week, "sites_seen": 0, "snapshots": 0, "updated": 0}
    domains = [s["domain"] for s in sites]

    # ONE bulk traffic call per market (each covers up to 1000 domains) instead of
    # one call per (site, market) — the difference between ~20 calls and ~20,000
    # for a 1000-site catalogue. etv_by_market[code] = {domain: etv in that market}.
    etv_by_market: dict[int, dict[str, float]] = {}
    for code in market_codes:
        lang = language_for(iso_for(code)) or "en"
        etv_by_market[code] = client.bulk_domain_traffic(domains, code, lang)

    updated = 0
    snapshotted = 0
    for site in sites:
        dom = site["domain"]
        by_country = {}
        for code in market_codes:
            etv = etv_by_market[code].get(dom, 0)
            if etv and etv > 0:
                by_country[iso_for(code)] = round(etv, 1)
        if not by_country:
            continue  # no API traffic anywhere — leave the row untouched

        total_etv = round(sum(by_country.values()), 1)
        top_country = max(by_country, key=by_country.get)
        # multi-region: tag the site to every market it draws traffic from
        _write_regions(conn, site["id"], by_country, total_etv, top_country)

        # append weekly snapshot (idempotent per site+week)
        conn.execute(
            """INSERT INTO traffic_snapshots (site_id, iso_week, etv, top_country, captured_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(site_id, iso_week) DO UPDATE SET
                 etv=excluded.etv, top_country=excluded.top_country""",
            (site["id"], iso_week, total_etv, top_country,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        snapshotted += 1

        trend = _compute_trend(conn, site["id"], iso_week, total_etv)
        fields = {
            "etv": total_etv,
            "top_country": top_country,
            "last_api_refresh": iso_week,
        }
        if trend:
            fields["trend_pct"], fields["trend_dir"] = trend

        # THE guarded write: only API-owned columns can be touched here.
        ownership.apply_api_update(conn, site["id"], fields, source="dataforseo:labs")
        updated += 1

    # Always auto-tag verticals as part of the weekly heartbeat — corrects
    # bulk-import placeholders and sweeps up any site that slipped through.
    # Human back-office overrides are skipped (radar/tagging.py).
    tagged = tagging.tag_all(conn)

    conn.commit()
    return {"iso_week": iso_week, "sites_seen": len(sites),
            "snapshots": snapshotted, "updated": updated,
            "verticals_retagged": tagged["retagged"]}
