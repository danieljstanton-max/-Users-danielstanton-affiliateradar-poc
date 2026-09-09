"""Affswap — the peer-to-peer swap engine.

Contacts are never sold, they are SWAPPED. The flow:

    register_have / register_want   a manager lists what they hold and what they need
    find_matches                    mutual pairs: A has what B wants AND B has what A wants
    agree                           each side agrees; the SECOND agree triggers the transfer
    (transfer)                      both contacts change hands, a ledger row per direction,
                                    and each side spends one swap from their plan allowance

Managers are the verified `chat_managers` (same identity, same gate). Everything
here is human/peer-owned — the weekly API refresh never touches these tables.

Stdlib only. The whole thing runs against the local SQLite DB with no network.
"""
from __future__ import annotations

import sqlite3

from datetime import datetime, timedelta, timezone

from . import config
from .db import now_iso


class SwapError(Exception):
    """A swap could not proceed (not verified, out of swaps, unknown manager…)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# --- plan / allowance helpers ----------------------------------------------
def plan_allowance(plan: str):
    """Monthly swap allowance for a plan. None == unlimited."""
    return config.SWAP_PLANS.get(plan, config.SWAP_PLANS["standard"])["swaps"]


def ensure_account(conn: sqlite3.Connection, manager_id: int,
                   plan: str = "standard") -> sqlite3.Row:
    row = conn.execute("SELECT * FROM swap_accounts WHERE manager_id=?",
                       (manager_id,)).fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO swap_accounts (manager_id, plan, period_start, updated_at) "
        "VALUES (?,?,?,?)", (manager_id, plan, now_iso(), now_iso()))
    conn.commit()
    return conn.execute("SELECT * FROM swap_accounts WHERE manager_id=?",
                        (manager_id,)).fetchone()


def set_plan(conn: sqlite3.Connection, manager_id: int, plan: str) -> None:
    if plan not in config.SWAP_PLANS:
        raise SwapError(f"unknown plan: {plan}")
    ensure_account(conn, manager_id)
    conn.execute("UPDATE swap_accounts SET plan=?, updated_at=? WHERE manager_id=?",
                 (plan, now_iso(), manager_id))
    conn.commit()


def grant_swaps(conn: sqlite3.Connection, manager_id: int, count: int = 1) -> int:
    """Add `count` swaps to a wallet — used by both paid top-ups and free rewards
    (an approved contribution). Returns the new remaining balance."""
    ensure_account(conn, manager_id)
    conn.execute("UPDATE swap_accounts SET extra_swaps=extra_swaps+?, updated_at=? "
                 "WHERE manager_id=?", (count, now_iso(), manager_id))
    conn.commit()
    return remaining(conn, manager_id)


def buy_extra(conn: sqlite3.Connection, manager_id: int, count: int = 1) -> int:
    """Top up `count` swaps at £3.99 each (no real payment in the PoC)."""
    return grant_swaps(conn, manager_id, count)


def remaining(conn: sqlite3.Connection, manager_id: int):
    """Swaps this manager can still spend. None == unlimited."""
    acct = ensure_account(conn, manager_id)
    allow = plan_allowance(acct["plan"])
    if allow is None:
        return None
    return max(0, allow - acct["swaps_used"]) + acct["extra_swaps"]


def _spend_one(conn: sqlite3.Connection, manager_id: int) -> None:
    """Consume one swap: allowance first, then a bought top-up. Caller must have
    already checked availability (unlimited plans never reach here to spend)."""
    acct = ensure_account(conn, manager_id)
    if plan_allowance(acct["plan"]) is None:
        return  # unlimited — nothing to decrement
    allow = plan_allowance(acct["plan"])
    if acct["swaps_used"] < allow:
        conn.execute("UPDATE swap_accounts SET swaps_used=swaps_used+1, updated_at=? "
                     "WHERE manager_id=?", (now_iso(), manager_id))
    elif acct["extra_swaps"] > 0:
        conn.execute("UPDATE swap_accounts SET extra_swaps=extra_swaps-1, updated_at=? "
                     "WHERE manager_id=?", (now_iso(), manager_id))
    else:
        raise SwapError("no swaps left")  # defensive; checked before calling


# --- registering wants / haves ---------------------------------------------
def _site_id(conn: sqlite3.Connection, site) -> int:
    if isinstance(site, int):
        return site
    row = conn.execute("SELECT id FROM sites WHERE domain=?", (site,)).fetchone()
    if not row:
        raise SwapError(f"unknown site: {site}")
    return row["id"]


def _site_approved(conn: sqlite3.Connection, sid: int) -> bool:
    """A site can be traded only once an admin has approved it as an affiliate.
    Wants/haves may be registered against any site (including one still in the
    review queue) — the gate is at the swap itself, not at registration."""
    row = conn.execute("SELECT classification FROM sites WHERE id=?", (sid,)).fetchone()
    return bool(row and row["classification"] == "affiliate")


def register_have(conn: sqlite3.Connection, manager_id: int, site) -> None:
    # Anyone may register a Have for any site, approved or not — the approval
    # gate applies when a swap actually transfers (see _transfer).
    sid = _site_id(conn, site)
    conn.execute("INSERT OR IGNORE INTO swap_haves (manager_id, site_id, created_at) "
                 "VALUES (?,?,?)", (manager_id, sid, now_iso()))
    # can't want what you already hold
    conn.execute("DELETE FROM swap_wants WHERE manager_id=? AND site_id=?", (manager_id, sid))
    conn.commit()


def register_want(conn: sqlite3.Connection, manager_id: int, site) -> None:
    sid = _site_id(conn, site)
    if conn.execute("SELECT 1 FROM swap_haves WHERE manager_id=? AND site_id=?",
                    (manager_id, sid)).fetchone():
        raise SwapError("you already have this contact")
    conn.execute("INSERT OR IGNORE INTO swap_wants (manager_id, site_id, created_at) "
                 "VALUES (?,?,?)", (manager_id, sid, now_iso()))
    conn.commit()


# --- the matching engine ----------------------------------------------------
def find_matches(conn: sqlite3.Connection, manager_id: int | None = None) -> list:
    """Find mutual swaps and persist them. A mutual swap exists between managers
    m and n when (m wants a site n has) AND (n wants a site m has). We keep at
    most one active match per pair. Returns the list of newly created match ids.

    If manager_id is given, only pairs involving that manager are scanned.
    """
    # who wants what / who has what
    wants: dict[int, set] = {}
    for r in conn.execute("SELECT manager_id, site_id FROM swap_wants"):
        wants.setdefault(r["manager_id"], set()).add(r["site_id"])
    haves: dict[int, set] = {}
    for r in conn.execute("SELECT manager_id, site_id FROM swap_haves"):
        haves.setdefault(r["manager_id"], set()).add(r["site_id"])

    managers = sorted(set(wants) | set(haves))
    created = []
    for i, m in enumerate(managers):
        if manager_id is not None and m != manager_id:
            # still allow m to be the *other* side of manager_id's pair below
            pass
        for n in managers[i + 1:]:
            if manager_id is not None and manager_id not in (m, n):
                continue
            m_gets = sorted(wants.get(m, set()) & haves.get(n, set()))
            n_gets = sorted(wants.get(n, set()) & haves.get(m, set()))
            if not (m_gets and n_gets):
                continue
            # one active match per pair
            if conn.execute(
                "SELECT 1 FROM swap_matches WHERE a_manager=? AND b_manager=? "
                "AND status!='void'", (m, n)).fetchone():
                continue
            # canonical: a_manager < b_manager (m < n already, by iteration order)
            a_gets, b_gets = m_gets[0], n_gets[0]
            cur = conn.execute(
                """INSERT OR IGNORE INTO swap_matches
                   (a_manager, b_manager, a_gets_site, b_gets_site, created_at)
                   VALUES (?,?,?,?,?)""", (m, n, a_gets, b_gets, now_iso()))
            if cur.lastrowid and cur.rowcount:
                created.append(cur.lastrowid)
    conn.commit()
    return created


# --- reading matches for a manager -----------------------------------------
def _handle(conn, mid):
    r = conn.execute("SELECT handle FROM chat_managers WHERE id=?", (mid,)).fetchone()
    return r["handle"] if r else f"#{mid}"


def _domain(conn, sid):
    r = conn.execute("SELECT domain FROM sites WHERE id=?", (sid,)).fetchone()
    return r["domain"] if r else f"site#{sid}"


def list_matches(conn: sqlite3.Connection, manager_id: int) -> list:
    """The 'Matches' view for one manager: who they matched with, what each side
    gives/gets, whether each has agreed, and whether it's completed."""
    rows = conn.execute(
        "SELECT * FROM swap_matches WHERE (a_manager=? OR b_manager=?) "
        "AND status!='void' ORDER BY created_at DESC", (manager_id, manager_id)).fetchall()
    out = []
    for r in rows:
        me_is_a = r["a_manager"] == manager_id
        other = r["b_manager"] if me_is_a else r["a_manager"]
        i_get = r["a_gets_site"] if me_is_a else r["b_gets_site"]
        they_get = r["b_gets_site"] if me_is_a else r["a_gets_site"]
        i_agreed = bool(r["a_agreed"] if me_is_a else r["b_agreed"])
        they_agreed = bool(r["b_agreed"] if me_is_a else r["a_agreed"])
        out.append({
            "match_id": r["id"],
            "with": _handle(conn, other), "with_id": other,
            "you_give": _domain(conn, they_get),   # you give them the contact they get
            "you_get": _domain(conn, i_get),
            "you_agreed": i_agreed, "they_agreed": they_agreed,
            "status": r["status"],
            "unlocked_contact": _contact_snapshot(conn, i_get) if r["status"] == "completed" else None,
        })
    return out


# --- agreeing + the transfer ------------------------------------------------
def _is_verified(conn, mid) -> bool:
    r = conn.execute("SELECT status FROM chat_managers WHERE id=?", (mid,)).fetchone()
    return bool(r and r["status"] == "verified")


def _contact_snapshot(conn, site_id: int) -> str:
    """The contact string exactly as it will be handed over (the evidence)."""
    c = conn.execute(
        "SELECT contact_email, contact_telegram, contact_teams FROM site_contacts "
        "WHERE site_id=?", (site_id,)).fetchone()
    if not c:
        return "(no contact on file)"
    parts = []
    if c["contact_email"]:    parts.append(c["contact_email"])
    if c["contact_telegram"]: parts.append(c["contact_telegram"])
    if c["contact_teams"]:    parts.append("Teams: " + c["contact_teams"])
    return " · ".join(parts) or "(no contact on file)"


def agree(conn: sqlite3.Connection, match_id: int, manager_id: int) -> dict:
    """Record this manager's agreement. If both have now agreed, run the
    transfer. Returns {status, ...}. When both agree but a side is out of swaps,
    the match stays 'ready' (both agreed) and we report needs_swaps so the app
    can prompt an upgrade / top-up."""
    m = conn.execute("SELECT * FROM swap_matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        raise SwapError("unknown match")
    if m["status"] == "completed":
        return {"status": "completed", "note": "already swapped"}
    if manager_id not in (m["a_manager"], m["b_manager"]):
        raise SwapError("not your match")
    if not _is_verified(conn, manager_id):
        raise SwapError("only verified managers can swap")

    col = "a_agreed" if manager_id == m["a_manager"] else "b_agreed"
    conn.execute(f"UPDATE swap_matches SET {col}=1 WHERE id=?", (match_id,))
    conn.commit()

    m = conn.execute("SELECT * FROM swap_matches WHERE id=?", (match_id,)).fetchone()
    if not (m["a_agreed"] and m["b_agreed"]):
        return {"status": "waiting", "you_agreed": True,
                "waiting_on": _handle(conn, m["b_manager"] if manager_id == m["a_manager"] else m["a_manager"])}
    return _transfer(conn, m)


def _transfer(conn: sqlite3.Connection, m: sqlite3.Row) -> dict:
    """Both agreed — hand over both contacts, write the ledger, spend a swap each.
    Blocks (without completing) if a site isn't approved yet, or if either side
    has no swaps left. The match stays agreed and completes later (a site being
    approved re-runs this via settle_ready_matches)."""
    a, b = m["a_manager"], m["b_manager"]
    # gate 1: a swap can't complete until BOTH affiliates are admin-approved
    unapproved = [_domain(conn, s) for s in (m["a_gets_site"], m["b_gets_site"])
                  if not _site_approved(conn, s)]
    if unapproved:
        return {"status": "pending_approval", "sites": unapproved,
                "note": "both agreed — the swap completes once an admin approves "
                        "the affiliate(s)"}
    # gate 2: each side must have a swap to spend
    ra, rb = remaining(conn, a), remaining(conn, b)
    broke = [_handle(conn, mid) for mid, rem in ((a, ra), (b, rb)) if rem == 0]
    if broke:
        return {"status": "needs_swaps", "who": broke,
                "topup": config.SWAP_TOPUP_PRICE,
                "note": "both agreed — waiting on a swap to spend"}

    # A receives a_gets_site (B provides it); B receives b_gets_site (A provides it)
    _record_transfer(conn, m["id"], from_mgr=b, to_mgr=a, site_id=m["a_gets_site"])
    _record_transfer(conn, m["id"], from_mgr=a, to_mgr=b, site_id=m["b_gets_site"])
    _spend_one(conn, a)
    _spend_one(conn, b)
    conn.execute("UPDATE swap_matches SET status='completed', completed_at=? WHERE id=?",
                 (now_iso(), m["id"]))
    conn.commit()
    # after spending, anyone now on 0 swaps (and out of trial) is locked out of
    # browsing until they buy/earn another — the app shows this notice.
    notices = []
    for mid in (a, b):
        ac = access(conn, mid)
        if ac["state"] == "locked":
            notices.append({"manager": _handle(conn, mid), "state": "locked",
                            "notice": "That was your last swap — browsing is locked until "
                                      "you buy or earn at least 1 more."})
    return {
        "status": "completed",
        "transfers": [
            {"to": _handle(conn, a), "site": _domain(conn, m["a_gets_site"]),
             "contact": _contact_snapshot(conn, m["a_gets_site"])},
            {"to": _handle(conn, b), "site": _domain(conn, m["b_gets_site"]),
             "contact": _contact_snapshot(conn, m["b_gets_site"])},
        ],
        "notices": notices,
    }


def _record_transfer(conn, match_id, from_mgr, to_mgr, site_id) -> None:
    ts, dom, contact = now_iso(), _domain(conn, site_id), _contact_snapshot(conn, site_id)
    conn.execute(
        """INSERT INTO swap_ledger
           (match_id, from_manager, to_manager, site_id, domain, contact_snapshot,
            transferred_at) VALUES (?,?,?,?,?,?,?)""",
        (match_id, from_mgr, to_mgr, site_id, dom, contact, ts))
    # mirror the shared contact to the Google Sheet in real time (best-effort —
    # a sheet hiccup must never break the swap itself)
    try:
        from . import sheets
        sheets.append_swap(conn, {"at": ts, "from": _handle(conn, from_mgr),
                                  "to": _handle(conn, to_mgr), "domain": dom,
                                  "contact": contact, "match_id": match_id})
    except Exception:
        pass


# --- the operator's ledger (back office) ------------------------------------
def ledger(conn: sqlite3.Connection, flagged_only: bool = False) -> list:
    q = ("SELECT * FROM swap_ledger " +
         ("WHERE flagged=1 " if flagged_only else "") +
         "ORDER BY transferred_at DESC")
    out = []
    for r in conn.execute(q):
        out.append({
            "id": r["id"], "match_id": r["match_id"],
            "from": _handle(conn, r["from_manager"]), "from_id": r["from_manager"],
            "to": _handle(conn, r["to_manager"]), "to_id": r["to_manager"],
            "domain": r["domain"], "contact": r["contact_snapshot"],
            "flagged": bool(r["flagged"]), "flag_reason": r["flag_reason"],
            "at": r["transferred_at"],
        })
    return out


def flag_transfer(conn: sqlite3.Connection, ledger_id: int, reason: str,
                  ban: bool = False) -> dict:
    """Operator marks a transfer as bad (e.g. wrong info sent). Optionally BANS
    the sender — same status used by the chat gate, so a ban here also cuts the
    manager out of the community chat and future swaps."""
    r = conn.execute("SELECT * FROM swap_ledger WHERE id=?", (ledger_id,)).fetchone()
    if not r:
        return {"ok": False}
    conn.execute("UPDATE swap_ledger SET flagged=1, flag_reason=? WHERE id=?",
                 (reason, ledger_id))
    banned = None
    if ban:
        conn.execute("UPDATE chat_managers SET status='banned' WHERE id=?",
                     (r["from_manager"],))
        banned = _handle(conn, r["from_manager"])
    conn.commit()
    return {"ok": True, "flagged": ledger_id, "banned": banned}


def is_banned(conn, manager_id: int) -> bool:
    row = conn.execute("SELECT status FROM chat_managers WHERE id=?", (manager_id,)).fetchone()
    return bool(row and row["status"] == "banned")


# --- access gate: 48h trial, then browsing needs at least 1 swap ------------
def approve_manager(conn: sqlite3.Connection, manager_id: int,
                    plan: str = "standard") -> dict:
    """Admin approves a signup: mark verified, start the 48h trial clock now,
    and open a swap wallet. This is what begins a member's access."""
    conn.execute("UPDATE chat_managers SET status='verified', approved_at=? WHERE id=?",
                 (now_iso(), manager_id))
    ensure_account(conn, manager_id, plan)
    conn.commit()
    return access(conn, manager_id)


def access(conn: sqlite3.Connection, manager_id: int) -> dict:
    """The single gate the app checks before showing site data / stats / reviews
    / traffic. States:
        trial   — inside the 48h window from approval (full access)
        active  — trial over but the member holds >= 1 swap (or unlimited plan)
        locked  — trial over and 0 swaps: must buy or earn a swap to browse
        unverified / banned — no access
    """
    m = conn.execute("SELECT status, approved_at FROM chat_managers WHERE id=?",
                     (manager_id,)).fetchone()
    if not m:
        return {"state": "unknown", "can_browse": False, "swaps": 0}
    rem = remaining(conn, manager_id)          # None == unlimited
    unlimited = rem is None
    trial_ends = None
    trial_hours_left = None
    in_trial = False
    start = _parse(m["approved_at"])
    if start:
        trial_ends = start + timedelta(hours=config.TRIAL_HOURS)
        secs = (trial_ends - _now()).total_seconds()
        in_trial = secs > 0
        trial_hours_left = round(max(0.0, secs) / 3600, 1)

    if m["status"] == "banned":
        state, can = "banned", False
    elif m["status"] != "verified":
        state, can = "unverified", False
    elif in_trial:
        state, can = "trial", True
    elif unlimited or (rem or 0) >= 1:
        state, can = "active", True
    else:
        state, can = "locked", False

    warn_last = (state == "active" and not unlimited and rem == 1)
    if state in ("banned", "unverified"):
        msg = ("No access — your account is banned." if state == "banned"
               else "Verify your account to get your 48-hour free trial.")
    elif state == "trial":
        msg = (f"Free trial — {trial_hours_left:g}h of full access left. "
               "After that you'll need at least 1 swap to browse.")
    elif state == "locked":
        msg = ("Your free trial has ended. Hold at least 1 swap to browse "
               "Affswap — buy one or earn one by adding a new affiliate.")
    elif warn_last:
        msg = ("This is your last swap. Once you use it you'll need to buy or "
               "earn at least 1 more to keep browsing Affswap.")
    elif unlimited:
        msg = "Unlimited plan — full access."
    else:
        msg = f"{rem} swaps in your bank — full access."

    return {
        "state": state, "can_browse": can,
        "in_trial": in_trial,
        "trial_ends_at": trial_ends.isoformat() if trial_ends else None,
        "trial_hours_left": trial_hours_left,
        "swaps": rem, "unlimited": unlimited,
        "warn_last_swap": warn_last,
        "topup": config.SWAP_TOPUP_PRICE,
        "message": msg,
    }


# --- loyalty: submit a new affiliate, earn a free swap on approval -----------
REWARD_SWAPS = 1   # free swaps granted when a submission is approved


def submit_contribution(conn: sqlite3.Connection, manager_id: int, url: str,
                        country: str | None = None, vertical: str | None = None,
                        comment: str | None = None) -> dict:
    """A manager submits an affiliate that isn't listed yet. The site is created
    as a CANDIDATE and enters the normal New-affiliate review queue with its
    market/vertical. NO swap is granted here — the reward only pays out if and
    when an admin APPROVES the site (see on_site_published)."""
    from .db import upsert_site
    from .importer import normalize_domain, resolve_country, VERTICALS
    from .locations import language_for
    if not _is_verified(conn, manager_id):
        raise SwapError("only verified managers can submit")
    domain = normalize_domain(url or "")
    if not domain:
        raise SwapError("enter a valid affiliate URL")
    listed = conn.execute("SELECT classification FROM sites WHERE domain=?", (domain,)).fetchone()
    if listed and listed["classification"] == "affiliate":
        raise SwapError("already listed — no reward for a site we already have")
    if conn.execute("SELECT 1 FROM swap_contributions WHERE domain=? AND status='pending'",
                    (domain,)).fetchone():
        raise SwapError("already submitted — pending review")

    iso = resolve_country(country) if country else None
    vraw = (vertical or "").strip().lower()
    vert = vraw if vraw in VERTICALS else None

    sid = upsert_site(conn, domain, source="manual")   # enters the queue as 'candidate'
    if vert:
        conn.execute("INSERT OR IGNORE INTO site_verticals (site_id, vertical) VALUES (?,?)",
                     (sid, vert))
    if iso and vert:
        if not conn.execute(
            "SELECT 1 FROM discovery_hits WHERE site_id=? AND country=? AND vertical=? "
            "AND keyword='(loyalty submission)'", (sid, iso, vert)).fetchone():
            conn.execute(
                """INSERT INTO discovery_hits (site_id, keyword, country, language, vertical,
                   rank_absolute, seen_at) VALUES (?,?,?,?,?,?,?)""",
                (sid, "(loyalty submission)", iso, language_for(iso), vert, None, now_iso()))

    cur = conn.execute(
        """INSERT INTO swap_contributions
           (manager_id, domain, country, vertical, comment, reward_swaps, site_id, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (manager_id, domain, iso or (country or None), vert or (vertical or None),
         (comment or None), REWARD_SWAPS, sid, now_iso()))
    conn.commit()
    return {"id": cur.lastrowid, "domain": domain, "site_id": sid,
            "status": "pending", "reward": REWARD_SWAPS,
            "note": "queued for admin review — swap granted only if approved"}


def list_contributions(conn: sqlite3.Connection, status: str = "pending") -> list:
    rows = conn.execute(
        "SELECT * FROM swap_contributions WHERE status=? ORDER BY created_at DESC",
        (status,)).fetchall()
    return [{
        "id": r["id"], "by": _handle(conn, r["manager_id"]), "by_id": r["manager_id"],
        "domain": r["domain"], "country": r["country"], "vertical": r["vertical"],
        "comment": r["comment"], "reward": r["reward_swaps"], "site_id": r["site_id"],
        "status": r["status"], "created_at": r["created_at"],
    } for r in rows]


def pending_contribution_for(conn: sqlite3.Connection, site_id: int) -> dict | None:
    """The loyalty submission awaiting this site's approval, if any — so the
    New-affiliate queue can show 'approving grants +N swap to X'."""
    r = conn.execute(
        "SELECT manager_id, reward_swaps FROM swap_contributions "
        "WHERE site_id=? AND status='pending'", (site_id,)).fetchone()
    return {"by": _handle(conn, r["manager_id"]), "reward": r["reward_swaps"]} if r else None


def on_site_published(conn: sqlite3.Connection, site_id: int,
                      by: str = "operator") -> dict | None:
    """Hook the New-affiliate queue APPROVE calls here. If the approved site came
    from a loyalty submission, grant the submitter their free swap now — this is
    the ONLY moment a reward swap is created, and only on admin approval."""
    r = conn.execute("SELECT * FROM swap_contributions WHERE site_id=? AND status='pending'",
                     (site_id,)).fetchone()
    if not r:
        return None
    left = grant_swaps(conn, r["manager_id"], r["reward_swaps"])
    conn.execute("UPDATE swap_contributions SET status='approved', resolved_at=?, "
                 "resolved_by=? WHERE id=?", (now_iso(), by, r["id"]))
    conn.commit()
    return {"granted": r["reward_swaps"], "to": _handle(conn, r["manager_id"]),
            "swaps_left": left, "domain": r["domain"]}


def settle_ready_matches(conn: sqlite3.Connection, site_id: int) -> list:
    """A site was just approved — complete any fully-agreed matches that were
    waiting only on it (both sides agreed, both sites now approved, swaps
    available). Returns the transfers that completed."""
    rows = conn.execute(
        "SELECT * FROM swap_matches WHERE status='ready' AND a_agreed=1 AND b_agreed=1 "
        "AND (a_gets_site=? OR b_gets_site=?)", (site_id, site_id)).fetchall()
    done = []
    for m in rows:
        res = _transfer(conn, m)
        if res.get("status") == "completed":
            done.append(res)
    return done


def on_site_rejected(conn: sqlite3.Connection, site_id: int,
                     by: str = "operator") -> dict | None:
    """Hook the queue REJECT calls here — a rejected submission earns nothing."""
    r = conn.execute("SELECT id, manager_id FROM swap_contributions "
                     "WHERE site_id=? AND status='pending'", (site_id,)).fetchone()
    if not r:
        return None
    conn.execute("UPDATE swap_contributions SET status='rejected', resolved_at=?, "
                 "resolved_by=? WHERE id=?", (now_iso(), by, r["id"]))
    conn.commit()
    return {"rejected": r["id"], "to": _handle(conn, r["manager_id"])}


# --- demo seeding -----------------------------------------------------------
# Verified managers (reuse the chat identity model). Sarah K is "me".
_DEMO_MANAGERS = [
    # cc_uid, handle, sector, years
    ("mgr_8f21c", "Sarah K", "Casino", 7),
    ("mgr_4a7d0", "Mike R", "Casino", 4),
    ("mgr_1c93e", "Priya N", "Sportsbook", 6),
    ("mgr_tomb0", "Tom B", "Bingo", 5),
]
# domain -> (email, telegram, teams)  — the contacts that will be swapped
_DEMO_CONTACTS = {
    "casinopilot-uk.example":     ("deals@casinopilot-uk.example", "@casinopilot_deals", None),
    "topcasinoreviews-uk.example":("partners@topcasinoreviews-uk.example", None, "partners@topcasinoreviews-uk.example"),
    "slotwise-uk.example":        (None, "@slotwise", None),
    "reelmentor.example":         ("hi@reelmentor.example", None, None),
    "bonusradar-uk.example":      (None, "@bonusradar", None),
}
# manager handle -> (haves, wants)
_DEMO_BOOK = {
    "Sarah K": (["slotwise-uk.example", "reelmentor.example"],
                ["casinopilot-uk.example", "topcasinoreviews-uk.example", "bonusradar-uk.example"]),
    "Mike R":  (["casinopilot-uk.example"], ["slotwise-uk.example"]),
    "Priya N": (["topcasinoreviews-uk.example"], ["reelmentor.example"]),
    "Tom B":   (["bonusradar-uk.example"], ["reelmentor.example"]),
}


def _mid(conn, handle):
    return conn.execute("SELECT id FROM chat_managers WHERE handle=?", (handle,)).fetchone()["id"]


def seed_demo(conn: sqlite3.Connection) -> dict:
    """Self-contained: creates the managers, the affiliates + their contacts, the
    haves/wants that mutually match, runs the matcher, and pre-agrees Sarah's
    match with Tom so the 'waiting on them' state is visible."""
    from . import ownership
    from .db import upsert_site

    # approved-hours-ago per manager: most are past the 48h trial, Tom is still in it
    _approved_ago = {"Sarah K": 60, "Mike R": 60, "Priya N": 60, "Tom B": 10}
    for uid, handle, sector, years in _DEMO_MANAGERS:
        appr = (_now() - timedelta(hours=_approved_ago.get(handle, 60))).replace(
            microsecond=0).isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO chat_managers
               (cc_uid, handle, sector, years, linkedin_verified, work_email_confirmed,
                status, real_name, approved_at, created_at)
               VALUES (?,?,?,?,1,1,'verified',?,?,?)""",
            (uid, handle, sector, years, handle, appr, now_iso()))
    conn.commit()

    for domain, (email, tg, teams) in _DEMO_CONTACTS.items():
        sid = upsert_site(conn, domain, source="manual")
        ownership.set_classification(conn, sid, "affiliate", admin="seed")
        ownership.upsert_contact(conn, sid, contact_email=email, contact_telegram=tg,
                                 contact_teams=teams, uploaded_by="seed")
    conn.commit()

    for handle, (haves, wants) in _DEMO_BOOK.items():
        mid = _mid(conn, handle)
        ensure_account(conn, mid, "standard")
        for d in haves:
            register_have(conn, mid, d)
        for d in wants:
            register_want(conn, mid, d)

    created = find_matches(conn)

    # pre-agree Sarah's side of the Tom match -> "waiting on Tom"
    sarah, tom = _mid(conn, "Sarah K"), _mid(conn, "Tom B")
    tom_match = conn.execute(
        "SELECT id FROM swap_matches WHERE (a_manager=? AND b_manager=?) "
        "OR (a_manager=? AND b_manager=?)", (sarah, tom, tom, sarah)).fetchone()
    if tom_match:
        agree(conn, tom_match["id"], sarah)

    # loyalty: a couple of new-affiliate submissions waiting for the Rewards queue
    for who, url, iso, vert, why in [
        ("Mike R", "newcasinohub.example", "GB", "casino",
         "Ranks page 1 for 'best uk casino bonuses' — not in our list yet."),
        ("Priya N", "punterspalace.example", "IE", "sportsbook",
         "Big Irish sportsbook affiliate, growing fast."),
    ]:
        try:
            submit_contribution(conn, _mid(conn, who), url, iso, vert, why)
        except SwapError:
            pass

    return {"managers": len(_DEMO_MANAGERS), "affiliates": len(_DEMO_CONTACTS),
            "matches": len(list_matches(conn, sarah)),
            "contributions": len(list_contributions(conn))}
