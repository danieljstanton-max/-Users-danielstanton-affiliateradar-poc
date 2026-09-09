"""CSV bulk import — the back-office path for adding sites and contacts at scale.

Matches existing sites by domain (creating new ones as source='manual'), and
writes only HUMAN-owned data: classification, market association, verticals and
the three contact channels. It never writes traffic/trend, so a bulk upload and
the weekly API refresh can't clobber each other.

Flexible headers (case-insensitive, several aliases accepted):

    url, country, vertical, company, contact_name, email, telegram, teams, status

Only `url` is required. `country`+`vertical` place the site in a market; contact
columns fill the outreach channels; `status` overrides classification
(defaults to `affiliate` for a manual upload — a human is vouching for it).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3

from . import locations, ownership, tagging
from .db import now_iso, upsert_site

# header alias -> canonical field
ALIASES = {
    "url": "url", "domain": "url", "website": "url", "site": "url",
    "website_name": "display_name", "display_name": "display_name",
    "site_name": "display_name", "displayname": "display_name",
    "country": "country", "geo": "country", "market": "country", "iso": "country",
    "vertical": "vertical", "category": "vertical", "type": "vertical",
    "company": "company", "company_name": "company", "brand": "company",
    "contact": "contact_name", "contact_name": "contact_name", "name": "contact_name",
    "email": "email", "e-mail": "email", "mail": "email",
    "telegram": "telegram", "tg": "telegram",
    "teams": "teams", "ms_teams": "teams", "microsoft_teams": "teams",
    "status": "status", "classification": "status",
}
VERTICALS = ("casino", "sportsbook", "bingo", "poker")
STATUSES = ("candidate", "affiliate", "rejected")

TEMPLATE = (
    "website_name,url,country,vertical,company,contact_name,email,telegram,teams,status\n"
    "Acme Casino Guide,https://acme-casino-guide.example,GB,casino,Acme Media Ltd,Jo Bloggs,deals@acme.example,@acme_deals,,affiliate\n"
    "BestSlots,bestslots.example,DE,casino,BestSlots GmbH,,,@bestslots,,affiliate\n"
    "Punters Pick,https://punterspick.example/,Ireland,sportsbook,Punters Pick,Sam Ryan,sam@punterspick.example,,sam@punterspick.example,affiliate\n"
)


_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](-?[a-z0-9])*\.)+[a-z]{2,}$")


BLACKLIST_ALIASES = {
    "url": "url", "domain": "url", "website": "url", "site": "url",
    "reason": "reason", "why": "reason", "note": "reason", "notes": "reason",
    "country": "country", "geo": "country", "market": "country",
    "company": "company", "company_name": "company", "brand": "company",
}
BLACKLIST_TEMPLATE = (
    "url,country,reason,company\n"
    "scam-casino-partners.example,GB,\"Non-payment of commissions — three managers reported unpaid Q2 2026 invoices.\",Scammy Media Ltd\n"
    "shadyslots-affiliate.example,DE,\"Cookie-stuffing and trademark bidding on our brand terms.\",\n"
    "https://ghostaffiliate.example/,GB,\"Went dark mid-campaign; contact unreachable for 6+ weeks.\",Ghost Media\n"
)


def normalize_domain(url: str) -> str | None:
    u = (url or "").strip().lower()
    if not u:
        return None
    u = re.sub(r"^https?://", "", u)
    u = u.split("/")[0].split("?")[0].split(":")[0]
    if u.startswith("www."):
        u = u[4:]
    return u if _DOMAIN_RE.match(u) else None


def resolve_country(val: str) -> str | None:
    v = (val or "").strip()
    if not v:
        return None
    up = v.upper()
    if len(up) == 2 and locations.code_for(up):
        return up
    for iso, _num, name in locations.COUNTRIES:
        if name.lower() == v.lower():
            return iso
    return None


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Parse CSV text into canonical-field dicts. Returns (rows, header_errors)."""
    text = (text or "").strip()
    if not text:
        return [], ["No CSV provided."]
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["CSV is empty."]

    colmap: dict[int, str] = {}
    for i, h in enumerate(header):
        key = ALIASES.get(h.strip().lower().replace(" ", "_"))
        if key:
            colmap[i] = key
    if "url" not in colmap.values():
        return [], ["CSV needs a 'url' (or 'domain'/'website') column. "
                    f"Found headers: {', '.join(header)}"]

    rows = []
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue
        rec = {v: "" for v in set(colmap.values())}
        for i, val in enumerate(raw):
            if i in colmap:
                rec[colmap[i]] = val.strip()
        rows.append(rec)
    return rows, []


def parse_blacklist_csv(text: str) -> tuple[list[dict], list[str]]:
    """Parse a blacklist CSV (url + reason [+ country, company])."""
    text = (text or "").strip()
    if not text:
        return [], ["No CSV provided."]
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["CSV is empty."]
    colmap = {}
    for i, h in enumerate(header):
        key = BLACKLIST_ALIASES.get(h.strip().lower().replace(" ", "_"))
        if key:
            colmap[i] = key
    have = set(colmap.values())
    if "url" not in have or "reason" not in have:
        return [], ["Blacklist CSV needs 'url' and 'reason' columns. "
                    f"Found: {', '.join(header)}"]
    rows = []
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue
        rec = {v: "" for v in have}
        for i, val in enumerate(raw):
            if i in colmap:
                rec[colmap[i]] = val.strip()
        rows.append(rec)
    return rows, []


def plan_blacklist(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    out = []
    for n, rec in enumerate(rows, start=1):
        domain = normalize_domain(rec.get("url", ""))
        item = {"row": n, "raw_url": rec.get("url", ""), "domain": domain,
                "action": "error", "message": "", "reason": rec.get("reason", "")}
        if not domain:
            item["message"] = "missing/invalid url"
            out.append(item); continue
        if not (rec.get("reason") or "").strip():
            item["message"] = "missing reason"
            out.append(item); continue
        exists = conn.execute(
            "SELECT 1 FROM blacklist b JOIN sites s ON s.id=b.site_id WHERE s.domain=?",
            (domain,)).fetchone()
        item["action"] = "update" if exists else "create"
        item["message"] = (rec["reason"][:80] + "…") if len(rec["reason"]) > 80 else rec["reason"]
        out.append(item)
    return out


def apply_blacklist(conn: sqlite3.Connection, rows: list[dict],
                    added_by: str = "csv_blacklist") -> dict:
    planned = plan_blacklist(conn, rows)
    created = updated = skipped = 0
    for rec, p in zip(rows, planned):
        if p["action"] == "error":
            skipped += 1
            continue
        domain = p["domain"]
        existing = conn.execute("SELECT id FROM sites WHERE domain=?", (domain,)).fetchone()
        if existing:
            sid = existing["id"]
        else:
            sid = upsert_site(conn, domain, source="manual")
            conn.execute("UPDATE sites SET source='manual' WHERE id=?", (sid,))
        # optional company + market association for context
        if rec.get("company"):
            cur = conn.execute("SELECT contact_email, contact_telegram, contact_teams, contact_name "
                               "FROM site_contacts WHERE site_id=?", (sid,)).fetchone()
            ownership.upsert_contact(
                conn, sid, company_name=rec["company"],
                contact_name=cur["contact_name"] if cur else None,
                contact_email=cur["contact_email"] if cur else None,
                contact_telegram=cur["contact_telegram"] if cur else None,
                contact_teams=cur["contact_teams"] if cur else None,
                uploaded_by=added_by)
        bl_country = resolve_country(rec.get("country", "")) if rec.get("country") else None
        was = conn.execute("SELECT 1 FROM blacklist WHERE site_id=?", (sid,)).fetchone()
        ownership.add_to_blacklist(conn, sid, rec["reason"].strip(),
                                   country=bl_country, admin=added_by)
        if was:
            updated += 1
        else:
            created += 1
    conn.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(rows)}


def plan(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Validate each row and describe what will happen — no writes."""
    out = []
    for n, rec in enumerate(rows, start=1):
        domain = normalize_domain(rec.get("url", ""))
        item = {"row": n, "raw_url": rec.get("url", ""), "domain": domain,
                "action": "error", "message": "", "country": None,
                "vertical": None, "status": None, "channels": []}
        if not domain:
            item["message"] = "missing/invalid url"
            out.append(item); continue

        vertical = (rec.get("vertical") or "casino").strip().lower()
        if vertical not in VERTICALS:
            item["message"] = f"unknown vertical '{vertical}'"
            out.append(item); continue
        item["vertical"] = vertical

        country = None
        if rec.get("country"):
            country = resolve_country(rec["country"])
            if not country:
                item["message"] = f"unknown country '{rec['country']}'"
                out.append(item); continue
        item["country"] = country

        status = (rec.get("status") or "affiliate").strip().lower()
        if status not in STATUSES:
            item["message"] = f"unknown status '{status}'"
            out.append(item); continue
        item["status"] = status

        item["channels"] = [c for c in ("email", "telegram", "teams") if rec.get(c)]
        exists = conn.execute("SELECT id FROM sites WHERE domain=?", (domain,)).fetchone()
        item["action"] = "update" if exists else "create"
        bits = []
        if country:
            bits.append(f"{locations.flag(country)} {country}")
        bits.append(vertical)
        if item["channels"]:
            bits.append("+".join(item["channels"]))
        item["message"] = " · ".join(bits)
        out.append(item)
    return out


def apply(conn: sqlite3.Connection, rows: list[dict], uploaded_by: str = "csv_import") -> dict:
    """Commit valid rows. Skips rows that fail validation (see plan())."""
    planned = plan(conn, rows)
    created = updated = skipped = 0

    for rec, p in zip(rows, planned):
        if p["action"] == "error":
            skipped += 1
            continue
        domain, country, vertical, status = p["domain"], p["country"], p["vertical"], p["status"]

        existing = conn.execute("SELECT id, source FROM sites WHERE domain=?", (domain,)).fetchone()
        if existing:
            sid = existing["id"]
            updated += 1
        else:
            sid = upsert_site(conn, domain, source="manual")
            conn.execute("UPDATE sites SET source='manual' WHERE id=?", (sid,))
            created += 1

        # classification (human-owned) — publishes if 'affiliate'
        ownership.set_classification(conn, sid, status, admin=uploaded_by)

        # website display name (human-owned) — only when the CSV supplied one,
        # so an import that omits the column never wipes an existing name.
        if rec.get("display_name"):
            ownership.set_display_name(conn, sid, rec["display_name"], admin=uploaded_by)

        # market association + vertical (so it shows in country browse/list)
        if country:
            already = conn.execute(
                "SELECT 1 FROM discovery_hits WHERE site_id=? AND country=? AND vertical=? AND keyword='(manual import)'",
                (sid, country, vertical)).fetchone()
            if not already:
                conn.execute(
                    """INSERT INTO discovery_hits (site_id, keyword, country, language, vertical, rank_absolute, seen_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (sid, "(manual import)", country, locations.language_for(country),
                     vertical, None, now_iso()))
        # The CSV vertical is a machine-owned starting point; the domain
        # classifier then refines it (e.g. an odds/sports site mis-labelled
        # 'casino' in bulk gets corrected). A human back-office edit wins over
        # both — add_auto_vertical / autotag_site skip human-owned sites.
        tagging.add_auto_vertical(conn, sid, vertical)
        tagging.autotag_site(conn, sid, domain)

        # contacts (human-owned) — MERGE so we never wipe an existing channel
        # that this CSV row simply didn't include.
        if any(rec.get(k) for k in ("company", "contact_name", "email", "telegram", "teams")):
            cur = conn.execute("SELECT * FROM site_contacts WHERE site_id=?", (sid,)).fetchone()
            def pick(col, key):
                return rec.get(key) or (cur[col] if cur else None)
            ownership.upsert_contact(
                conn, sid,
                company_name=pick("company_name", "company"),
                contact_name=pick("contact_name", "contact_name"),
                contact_email=pick("contact_email", "email"),
                contact_telegram=pick("contact_telegram", "telegram"),
                contact_teams=pick("contact_teams", "teams"),
                uploaded_by=uploaded_by)

    conn.commit()
    return {"created": created, "updated": updated, "skipped": skipped,
            "total": len(rows)}
