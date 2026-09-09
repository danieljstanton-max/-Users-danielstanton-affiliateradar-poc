"""Affswap alerts engine.

A member arms a rule from the app: a country (or all markets), one or more
verticals, the triggers they care about (a **new** affiliate detected, traffic
**up**, traffic **down**), and how they want to hear about it (push / email /
Telegram).

`evaluate()` is the engine tick — run it after a weekly refresh. For every active
rule it finds the affiliates in that market/vertical whose current state matches a
trigger and fires an event, which is handed to `deliver()`. Everything here is
real and DB-backed; **`deliver()` is the single plug-in point** where the push /
email / Telegram providers get wired in (marked CONFIRM below).

Stdlib only.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config
from .branding import display_name
from .db import now_iso
from .locations import flag, name

TRIGGERS = ("new", "up", "down")
CHANNELS = ("push", "email", "telegram")
VERTICALS = ("casino", "sportsbook", "bingo", "poker")
NEW_WINDOW_DAYS = 14                       # a site reads as "new" for this long
_TRIGGER_LABEL = {"new": "✦ New affiliate", "up": "▲ Traffic up", "down": "▼ Traffic down"}


class AlertError(Exception):
    """A rule could not be saved (bad vertical / trigger / channel / country)."""


# --- small helpers ----------------------------------------------------------
def _csv(items) -> str:
    return ",".join(items)


def _split(s: str) -> list:
    return [x for x in (s or "").split(",") if x]


def _clean(values, allowed, kind) -> list:
    out, seen = [], set()
    for v in values or []:
        v = (v or "").strip().lower()
        if not v or v in seen:
            continue
        if v not in allowed:
            raise AlertError(f"unknown {kind}: {v}")
        seen.add(v); out.append(v)
    if not out:
        raise AlertError(f"pick at least one {kind}")
    return out


def _resolve_country(country):
    """Accept an ISO code, a country name, or blank/'all' for every market."""
    c = (country or "").strip()
    if not c or c.lower() in ("all", "all markets", "world", "🌍 all markets"):
        return None
    from .importer import resolve_country
    iso = resolve_country(c)
    if not iso:
        raise AlertError(f"unknown country: {country}")
    return iso


def _recent(published_at) -> bool:
    if not published_at:
        return False
    try:
        d = datetime.fromisoformat(published_at)
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days <= NEW_WINDOW_DAYS


# --- rule CRUD --------------------------------------------------------------
def save_rule(conn: sqlite3.Connection, manager_id: int, country,
              verticals, triggers, delivery, active: int = 1) -> dict:
    iso = _resolve_country(country)
    v = _clean(verticals, VERTICALS, "vertical")
    t = _clean(triggers, TRIGGERS, "trigger")
    d = _clean(delivery, CHANNELS, "delivery channel")
    cur = conn.execute(
        """INSERT INTO alert_rules (manager_id, country, verticals, triggers, delivery,
           active, created_at) VALUES (?,?,?,?,?,?,?)""",
        (manager_id, iso, _csv(v), _csv(t), _csv(d), 1 if active else 0, now_iso()))
    conn.commit()
    return _rule_row(conn, cur.lastrowid)


def _rule_row(conn, rule_id):
    r = conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
    return _rule_dict(r) if r else None


def _rule_dict(r) -> dict:
    verts, trigs, dels = _split(r["verticals"]), _split(r["triggers"]), _split(r["delivery"])
    where = f"{flag(r['country'])} {name(r['country'])}" if r["country"] else "🌍 All markets"
    summary = (f"{where} · {', '.join(v.title() for v in verts)} · "
               f"{' '.join(_TRIGGER_LABEL[t] for t in trigs)} · via {', '.join(dels)}")
    return {"id": r["id"], "manager_id": r["manager_id"], "country": r["country"],
            "verticals": verts, "triggers": trigs, "delivery": dels,
            "active": bool(r["active"]), "created_at": r["created_at"], "summary": summary}


def list_rules(conn: sqlite3.Connection, manager_id: int | None = None) -> list:
    if manager_id is None:
        rows = conn.execute("SELECT * FROM alert_rules ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM alert_rules WHERE manager_id=? ORDER BY created_at DESC",
                            (manager_id,)).fetchall()
    return [_rule_dict(r) for r in rows]


def set_active(conn: sqlite3.Connection, rule_id: int, active: bool) -> None:
    conn.execute("UPDATE alert_rules SET active=? WHERE id=?", (1 if active else 0, rule_id))
    conn.commit()


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
    conn.commit()


# --- the engine: evaluate rules against current data ------------------------
def _matches_for_rule(conn, rule: dict) -> list:
    """Affiliates in the rule's market/vertical whose state fires one of its
    triggers, right now. Reads the same fields the refresh writes."""
    verts = rule["verticals"]
    q = ("SELECT DISTINCT s.id, s.domain, s.display_name, s.published_at, "
         "s.trend_pct, s.trend_dir "
         "FROM sites s JOIN discovery_hits h ON h.site_id=s.id "
         "WHERE s.classification='affiliate' "
         "AND s.id NOT IN (SELECT site_id FROM blacklist) ")
    params: list = []
    if rule["country"]:
        q += "AND h.country=? "; params.append(rule["country"])
    q += "AND h.vertical IN (%s)" % ",".join("?" * len(verts)); params += verts
    events = []
    for s in conn.execute(q, params).fetchall():
        for trig in rule["triggers"]:
            if trig == "new" and _recent(s["published_at"]):
                detail = "just published"
            elif trig == "up" and s["trend_dir"] == "up":
                detail = f"+{s['trend_pct']}%" if s["trend_pct"] is not None else "rising"
            elif trig == "down" and s["trend_dir"] == "down":
                detail = f"{s['trend_pct']}%" if s["trend_pct"] is not None else "falling"
            else:
                continue
            events.append({
                "rule_id": rule["id"], "manager_id": rule["manager_id"],
                "site_id": s["id"], "domain": s["domain"],
                "name": display_name(s["domain"], s["display_name"]),
                "event": trig, "detail": detail, "delivery": rule["delivery"],
            })
    return events


def evaluate(conn: sqlite3.Connection, manager_id: int | None = None,
             record: bool = False) -> list:
    """Run the engine over active rules and return the events that fire. With
    record=True, persist each event and dispatch it through deliver()."""
    rules = [r for r in list_rules(conn, manager_id) if r["active"]]
    fired = []
    for rule in rules:
        for ev in _matches_for_rule(conn, rule):
            if record:
                # idempotent delivery: don't notify twice for the same rule+site+event
                if conn.execute("SELECT 1 FROM alert_events WHERE rule_id=? AND site_id=? "
                                "AND event=?", (ev["rule_id"], ev["site_id"], ev["event"])
                                ).fetchone():
                    continue
                sent = deliver(ev, rule["delivery"])
                conn.execute(
                    """INSERT INTO alert_events (rule_id, manager_id, site_id, domain,
                       event, detail, delivered, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (ev["rule_id"], ev["manager_id"], ev["site_id"], ev["domain"],
                     ev["event"], ev["detail"], _csv(sent), now_iso()))
                ev["delivered"] = sent
            fired.append(ev)
    if record:
        conn.commit()
    return fired


def deliver(event: dict, channels: list) -> list:
    """THE PLUG-IN POINT. Dispatch one alert event to each chosen channel and
    return the channels actually sent. In the PoC this is a no-op that just
    acknowledges the channels; wire the real providers here — nothing else in
    the engine changes.

        push     -> APNs / FCM              # CONFIRM: device-token store + provider
        email    -> SMTP / SendGrid / SES   # CONFIRM: from-address + template
        telegram -> Telegram Bot API         # CONFIRM: bot token + chat id per user

    Kept deliberately tiny and side-effect-free so it's obvious where to plug in.
    """
    return [c for c in channels if c in CHANNELS]


# --- demo seeding -----------------------------------------------------------
def seed_demo(conn: sqlite3.Connection, manager_id: int | None = None) -> dict:
    """Arm a few rules for a manager so `evaluate` has something to fire against
    the demo DB. Reuses a verified manager if one exists."""
    if manager_id is None:
        row = conn.execute("SELECT id FROM chat_managers ORDER BY id LIMIT 1").fetchone()
        if not row:
            raise AlertError("no managers — seed chat/swap managers first")
        manager_id = row["id"]
    made = []
    for country, verts, trigs, dels in [
        ("GB", ["casino", "sportsbook"], ["new", "up", "down"], ["push", "email"]),
        ("DE", ["casino"], ["down"], ["push"]),
        (None, ["casino"], ["new"], ["telegram"]),   # all-markets new-affiliate radar
    ]:
        try:
            made.append(save_rule(conn, manager_id, country, verts, trigs, dels))
        except AlertError:
            pass
    return {"manager_id": manager_id, "rules": len(made)}
